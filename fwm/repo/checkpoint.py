import os
import re
import shutil
from typing import List, Optional

from fwm.data_loader.checkpoint import read_checkpoint_header, checkpoint_content_sha256, CheckpointFormatError
from fwm.database.catalog_store import save_catalog, load_catalog
from fwm.database.schema import DecoupledCheckpoint, CheckpointHart, DecoupledTrace
from fwm.repo.errors import AlreadyExistsError, FwmValidationError, NotFoundError
from fwm.repo.repo import Repo, DIGEST_ALGO, REPO_PERM_MASK
from fwm.repo.trace import resolve_trace, trace_harts
from fwm.utils.file_catalog import create_catalog, verify_catalog, FailedDetail

# The checkpoint names FESVR accepts in a checkpoint description file (riscv-fesvr/fesvr/ckpt_desc_reader.cc)
CHECKPOINT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# The riscv-simenv naming convention of SimPoint checkpoints: {entry}.{simpoint_index}.{weight:0.2f}
SIMPOINT_NAME_RE = re.compile(r"^.+\.\d+\.(\d+\.\d+)$")


def decode_simpoint_weight(name: str) -> Optional[float]:
    """
    The SimPoint weight encoded in a checkpoint name, e.g. 0.25 for "bfs-road-n64.3.0.25", or None.
    """
    match = SIMPOINT_NAME_RE.match(name)
    return float(match.group(1)) if match else None


def resolve_checkpoint(ref: str) -> DecoupledCheckpoint:
    """
    Find a checkpoint by "<trace>:<name>" (see resolve_trace() for <trace>), or by its name when it is unique.
    """
    if ":" in ref:
        trace_ref, name = ref.split(":", 1)
        trace = resolve_trace(trace_ref)
        ckpt = DecoupledCheckpoint.get_or_none((DecoupledCheckpoint.trace == trace) &
                                               (DecoupledCheckpoint.name == name))
        if ckpt is None:
            raise NotFoundError(f"trace {trace.uuid} has no checkpoint named '{name}'")
        return ckpt

    matches = list(DecoupledCheckpoint.select().where(DecoupledCheckpoint.name == ref))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise NotFoundError(f"no checkpoint named '{ref}'")
    raise FwmValidationError(
        f"{len(matches)} traces have a checkpoint named '{ref}', give it as <trace>:<name>: " +
        ", ".join(f"{c.trace.uuid}:{c.name}" for c in matches))


def checkpoint_harts(ckpt: DecoupledCheckpoint) -> List[CheckpointHart]:
    return list(ckpt.harts.order_by(CheckpointHart.hart_id))


def _existing_checkpoint(ckpt: DecoupledCheckpoint, content_sha256: Optional[str], file: str,
                         exist_ok: bool) -> DecoupledCheckpoint:
    """
    The answer to importing a checkpoint under a name its trace already has: that checkpoint with --exist-ok, if the
    content is the same, else an error.

    :param content_sha256: The content digest of the file being imported, if already computed.
    """
    what = f"trace {ckpt.trace.uuid} already has a checkpoint named '{ckpt.name}'"
    if not exist_ok:
        raise AlreadyExistsError(what)
    if content_sha256 is None:
        try:
            content_sha256 = checkpoint_content_sha256(file)
        except (CheckpointFormatError, OSError) as e:
            raise FwmValidationError(f"invalid checkpoint {file}: {e}")
    if content_sha256 != ckpt.content_sha256:
        raise AlreadyExistsError(f"{what}, with different content")
    return ckpt


def add_checkpoint(repo: Repo, trace_ref: str, file: str, name: Optional[str] = None,
                   simpoint_weight: Optional[float] = None, description: Optional[str] = None,
                   exist_ok: bool = False) -> DecoupledCheckpoint:
    """
    Import a checkpoint created from a trace. It is checked against the trace, so that it is known to load with it.

    :param name: The checkpoint's name, by default the file name.
    :param simpoint_weight: Its SimPoint weight, by default decoded from its name when it follows the riscv-simenv
                            convention {entry}.{simpoint_index}.{weight}.
    :param exist_ok: If the trace has a checkpoint of this name with the same content, return it instead of failing.
    :raise AlreadyExistsError: The trace has a checkpoint of this name.
    """
    trace: DecoupledTrace = resolve_trace(trace_ref)
    if not os.path.isfile(file):
        raise NotFoundError(f"{file} is not a file")
    if name is None:
        name = os.path.basename(file)
    if not CHECKPOINT_NAME_RE.match(name) or name in (".", ".."):
        raise FwmValidationError(
            f"invalid checkpoint name '{name}': a name may contain only letters, digits, '.', '_' and '-'")
    if simpoint_weight is None:
        simpoint_weight = decode_simpoint_weight(name)
    if simpoint_weight is not None and not 0 <= simpoint_weight <= 1:
        raise FwmValidationError(f"the SimPoint weight {simpoint_weight} of '{name}' is not in [0, 1]")

    existing = DecoupledCheckpoint.get_or_none((DecoupledCheckpoint.trace == trace) &
                                               (DecoupledCheckpoint.name == name))
    if existing is not None:
        return _existing_checkpoint(existing, None, file, exist_ok)

    with repo.staging() as staged:
        os.mkdir(staged)
        shutil.copy2(file, os.path.join(staged, name))
        return _import_checkpoint(repo, trace, file, name, staged, simpoint_weight, description, exist_ok)


def _import_checkpoint(repo: Repo, trace: DecoupledTrace, file: str, name: str, staged: str,
                       simpoint_weight: Optional[float], description: Optional[str],
                       exist_ok: bool) -> DecoupledCheckpoint:
    # check the copy that is stored, not the source
    staged_file = os.path.join(staged, name)
    try:
        header = read_checkpoint_header(staged_file)
        csr_counts = [hart.csr_count for hart in header.harts]
        content_sha256 = checkpoint_content_sha256(staged_file)
    except (CheckpointFormatError, OSError) as e:
        raise FwmValidationError(f"invalid checkpoint {file}: {e}")

    def mismatch(what: str) -> FwmValidationError:
        return FwmValidationError(f"checkpoint {file} doesn't belong to trace {trace.uuid}: {what}")

    if header.num_harts != trace.num_harts:
        raise mismatch(f"it has {header.num_harts} HART(s), the trace has {trace.num_harts}")
    if header.mem_mb != trace.mem_mb:
        raise mismatch(f"it is of a {header.mem_mb} MB system, the trace of a {trace.mem_mb} MB one")
    if header.boot_elf_sha256 != trace.boot_elf_sha256:
        raise mismatch(f"its boot ELF has SHA256 {header.boot_elf_sha256}, the trace's {trace.boot_elf_sha256}")
    for ckpt_hart, rec_hart in zip(header.harts, trace_harts(trace)):
        if ckpt_hart.recording_sha256 != rec_hart.recording_sha256:
            raise mismatch(f"HART {ckpt_hart.hart_id} was created from the recording of SHA256 "
                           f"{ckpt_hart.recording_sha256}, the trace's is {rec_hart.recording_sha256}")
        if ckpt_hart.cmd_index > rec_hart.num_commands:
            raise mismatch(f"HART {ckpt_hart.hart_id} resumes at command {ckpt_hart.cmd_index}, but the trace has only "
                           f"{rec_hart.num_commands}")

    catalog = create_catalog(DIGEST_ALGO, staged)
    with repo.importing() as place:
        if DecoupledTrace.get_or_none(DecoupledTrace.uuid == trace.uuid) is None:
            raise NotFoundError(f"trace {trace.uuid} was removed during the import")
        # another import of this name may have committed meanwhile
        existing = DecoupledCheckpoint.get_or_none((DecoupledCheckpoint.trace == trace) &
                                                   (DecoupledCheckpoint.name == name))
        if existing is not None:
            return _existing_checkpoint(existing, content_sha256, file, exist_ok)
        ckpt = DecoupledCheckpoint.create(
            trace=trace, name=name, file_catalog=save_catalog(catalog), format_version=header.format_version,
            content_sha256=content_sha256, simpoint_weight=simpoint_weight, description=description)
        for hart, csr_count in zip(header.harts, csr_counts):
            CheckpointHart.create(checkpoint=ckpt, hart_id=hart.hart_id, recording_sha256=hart.recording_sha256,
                                  cmd_index=hart.cmd_index, hart_state_version=hart.state_version,
                                  csr_count=csr_count)
        final_dir = repo.checkpoint_dir(trace.uuid)
        os.makedirs(final_dir, exist_ok=True)
        os.chmod(final_dir, 0o755)  # it holds the read-only checkpoints of the trace, but stays writable itself
        place(staged_file, os.path.join(final_dir, name))
    return ckpt


def verify_checkpoint(repo: Repo, ckpt: DecoupledCheckpoint) -> List[FailedDetail]:
    """
    Verify a checkpoint in the repository against its catalog, ignoring the write bits it lost on import.
    """
    file_problems, link_problems, dir_problems = verify_catalog(
        load_catalog(ckpt.file_catalog), repo.checkpoint_dir(ckpt.trace.uuid), perm_mask=REPO_PERM_MASK)
    return sorted(file_problems + link_problems + dir_problems)
