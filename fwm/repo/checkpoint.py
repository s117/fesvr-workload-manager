import json
import os
import re
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import pydantic

from fwm.data_loader.checkpoint import (
    read_checkpoint_header, checkpoint_content_sha256, CheckpointFormatError, CheckpointHeader
)
from fwm.data_loader.ckpt_desc import read_ckpt_desc, CkptDescError
from fwm.data_loader.simpoint_info import load_simpoint_info, SimpointInfo
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


@dataclass
class _NewCheckpoint:
    """A checkpoint being imported, checked against its trace."""
    file: str
    name: str
    staged_dir: str
    header: CheckpointHeader
    csr_counts: List[int]
    content_sha256: str
    simpoint_weight: Optional[float]
    simpoint_index: Optional[int]
    simpoint_interval: Optional[int]
    requested_count: Optional[int]


def _check_against_trace(trace: DecoupledTrace, file: str, header: CheckpointHeader) -> None:
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


def _checkpoint_names(files: Sequence[str], name: Optional[str]) -> List[str]:
    names = [name] if name is not None else [os.path.basename(f) for f in files]
    for n in names:
        if not CHECKPOINT_NAME_RE.match(n) or n in (".", ".."):
            raise FwmValidationError(
                f"invalid checkpoint name '{n}': a name may contain only letters, digits, '.', '_' and '-'")
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise FwmValidationError(f"several files would be named {', '.join(duplicates)}")
    return names


def _requested_counts(desc: Optional[str], names: List[str]) -> Dict[str, int]:
    """The instruction count each checkpoint was requested at, from the description file it was created from. The
    files must be exactly the checkpoints it lists."""
    if desc is None:
        return {}
    try:
        counts = dict(read_ckpt_desc(desc))
    except OSError as e:
        raise NotFoundError(f"cannot read the checkpoint description {desc}: {e}")
    except CkptDescError as e:
        raise FwmValidationError(f"invalid checkpoint description {desc}: {e}")
    missing = sorted(set(counts) - set(names), key=lambda n: counts[n])
    extra = sorted(set(names) - set(counts))
    problems = []
    if missing:
        problems.append("these checkpoints of the description weren't created (a count at or after the program's "
                        "exit creates none): " + ", ".join(f"{n} ({counts[n]})" for n in missing))
    if extra:
        problems.append(f"these files aren't in the description: {', '.join(extra)}")
    if problems:
        raise FwmValidationError(f"the checkpoints don't match {desc}: " + "; ".join(problems))
    return counts


def _simpoint_infos(path: Optional[str], names: List[str]) -> Dict[str, SimpointInfo]:
    if path is None:
        return {}
    try:
        infos = load_simpoint_info(path)
    except OSError as e:
        raise NotFoundError(f"cannot read the SimPoint information {path}: {e}")
    except (json.JSONDecodeError, pydantic.ValidationError) as e:
        raise FwmValidationError(f"invalid SimPoint information {path}: {e}")
    unknown = sorted(set(infos) - set(names))
    if unknown:
        raise FwmValidationError(
            f"the SimPoint information {path} names checkpoints not imported: {', '.join(unknown)}")
    return infos


def add_checkpoints(repo: Repo, trace_ref: str, files: Sequence[str], name: Optional[str] = None,
                    simpoint_weight: Optional[float] = None, simpoint_info: Optional[str] = None,
                    desc: Optional[str] = None, description: Optional[str] = None, exist_ok: bool = False,
                    sim_version: Optional[str] = None) -> List[DecoupledCheckpoint]:
    """
    Import checkpoints created from a trace, in one transaction. Each is checked against the trace, so that it is known
    to load with it.

    :param files: The checkpoint files. Each checkpoint is named after its file.
    :param name: The name of the checkpoint, instead of its file name (one file only).
    :param simpoint_weight: Its SimPoint weight (one file only).
    :param simpoint_info: A JSON file of SimPoint information (index, interval and weight) by checkpoint name.
    :param desc: The description file the checkpoints were created from: the files must be exactly the checkpoints it
                 lists, and it gives the instruction count each one was requested at.
    :param exist_ok: If the trace has a checkpoint of a name with the same content, keep it instead of failing.
    :param sim_version: The `spike --version` output of the simulator that created them, for provenance.
    :return: The checkpoints, in the order of the files.
    :raise AlreadyExistsError: The trace has a checkpoint of one of the names.

    Without a weight from simpoint_weight or simpoint_info, it is decoded from the name, when the name follows the
    riscv-simenv convention {entry}.{simpoint_index}.{weight}.
    """
    trace: DecoupledTrace = resolve_trace(trace_ref)
    if not files:
        raise FwmValidationError("no checkpoint file given")
    if len(files) > 1 and (name is not None or simpoint_weight is not None):
        raise FwmValidationError("a name or a SimPoint weight can be given for one checkpoint file only")
    if simpoint_weight is not None and simpoint_info is not None:
        raise FwmValidationError("give the SimPoint weight in the SimPoint information file")
    for file in files:
        if not os.path.isfile(file):
            raise NotFoundError(f"{file} is not a file")
    names = _checkpoint_names(files, name)
    requested = _requested_counts(desc, names)
    infos = _simpoint_infos(simpoint_info, names)

    result: Dict[str, DecoupledCheckpoint] = {}
    todo = []
    for file, n in zip(files, names):
        existing = DecoupledCheckpoint.get_or_none((DecoupledCheckpoint.trace == trace) &
                                                   (DecoupledCheckpoint.name == n))
        if existing is not None:
            result[n] = _existing_checkpoint(existing, None, file, exist_ok)
        else:
            todo.append((file, n))

    with repo.staging() as staged:
        os.mkdir(staged)
        new = []
        for i, (file, n) in enumerate(todo):
            staged_dir = os.path.join(staged, str(i))
            os.mkdir(staged_dir)
            staged_file = os.path.join(staged_dir, n)
            try:
                shutil.copyfile(file, staged_file)
                # check the copy that is stored, not the source
                header = read_checkpoint_header(staged_file)
                csr_counts = [hart.csr_count for hart in header.harts]
                content_sha256 = checkpoint_content_sha256(staged_file)
            except (CheckpointFormatError, OSError) as e:
                raise FwmValidationError(f"invalid checkpoint {file}: {e}")
            _check_against_trace(trace, file, header)

            info = infos.get(n)
            weight = simpoint_weight
            if weight is None and info is not None:
                weight = info.weight
            if weight is None:
                weight = decode_simpoint_weight(n)
            if weight is not None and not 0 <= weight <= 1:
                raise FwmValidationError(f"the SimPoint weight {weight} of '{n}' is not in [0, 1]")
            new.append(_NewCheckpoint(
                file=file, name=n, staged_dir=staged_dir, header=header, csr_counts=csr_counts,
                content_sha256=content_sha256, simpoint_weight=weight,
                simpoint_index=info.simpoint_index if info else None, simpoint_interval=info.interval if info else None,
                requested_count=requested.get(n)))

        with repo.importing() as place:
            if DecoupledTrace.get_or_none(DecoupledTrace.uuid == trace.uuid) is None:
                raise NotFoundError(f"trace {trace.uuid} was removed during the import")
            final_dir = repo.checkpoint_dir(trace.uuid)
            for c in new:
                # another import of this name may have committed meanwhile
                existing = DecoupledCheckpoint.get_or_none((DecoupledCheckpoint.trace == trace) &
                                                           (DecoupledCheckpoint.name == c.name))
                if existing is not None:
                    result[c.name] = _existing_checkpoint(existing, c.content_sha256, c.file, exist_ok)
                    continue
                ckpt = DecoupledCheckpoint.create(
                    trace=trace, name=c.name, file_catalog=save_catalog(create_catalog(DIGEST_ALGO, c.staged_dir)),
                    format_version=c.header.format_version, content_sha256=c.content_sha256,
                    simpoint_weight=c.simpoint_weight, simpoint_index=c.simpoint_index,
                    simpoint_interval=c.simpoint_interval, requested_count=c.requested_count,
                    sim_version=sim_version, description=description)
                for hart, csr_count in zip(c.header.harts, c.csr_counts):
                    CheckpointHart.create(checkpoint=ckpt, hart_id=hart.hart_id,
                                          recording_sha256=hart.recording_sha256, cmd_index=hart.cmd_index,
                                          hart_state_version=hart.state_version, csr_count=csr_count)
                os.makedirs(final_dir, exist_ok=True)
                os.chmod(final_dir, 0o755)  # it holds the read-only checkpoints of the trace, but stays writable itself
                place(os.path.join(c.staged_dir, c.name), os.path.join(final_dir, c.name))
                result[c.name] = ckpt
    return [result[n] for n in names]


def add_checkpoint(repo: Repo, trace_ref: str, file: str, name: Optional[str] = None,
                   simpoint_weight: Optional[float] = None, description: Optional[str] = None,
                   exist_ok: bool = False, sim_version: Optional[str] = None) -> DecoupledCheckpoint:
    """Import one checkpoint created from a trace; see add_checkpoints()."""
    return add_checkpoints(repo, trace_ref, [file], name=name, simpoint_weight=simpoint_weight,
                           description=description, exist_ok=exist_ok, sim_version=sim_version)[0]


def verify_checkpoint(repo: Repo, ckpt: DecoupledCheckpoint) -> List[FailedDetail]:
    """
    Verify a checkpoint in the repository against its catalog, ignoring the write bits it lost on import.
    """
    file_problems, link_problems, dir_problems = verify_catalog(
        load_catalog(ckpt.file_catalog), repo.checkpoint_dir(ckpt.trace.uuid), perm_mask=REPO_PERM_MASK)
    return sorted(file_problems + link_problems + dir_problems)
