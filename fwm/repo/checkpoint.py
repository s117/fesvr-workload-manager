import json
import os
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pydantic

from fwm.data_loader.checkpoint import (
    read_checkpoint_header, checkpoint_content_sha256, CheckpointFormatError, CheckpointHeader
)
from fwm.data_loader.ckpt_desc import read_ckpt_desc, CkptDescError
from fwm.data_loader.simpoint_info import load_simpoint_info, SimpointInfo
from fwm.database.catalog_store import save_catalog, load_catalog
from fwm.database.schema import DecoupledCheckpoint, CheckpointHart, DecoupledTrace
from fwm.repo.errors import AlreadyExistsError, FwmValidationError, NotFoundError
from fwm.repo.lookup import resolve_trace, trace_harts
from fwm.repo.repo import Repo, DIGEST_ALGO, REPO_PERM_MASK
from fwm.utils.file_catalog import create_catalog, verify_catalog, FailedDetail


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
    simpoint_max_k: Optional[int]
    requested_count: int


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


def _requested_checkpoints(desc: str, checkpoint_dir: str) -> List[Tuple[str, int, str]]:
    """
    The checkpoints a description file lists, with the instruction count each was requested at, and its file in the
    checkpoint directory. Other files in the directory are ignored.

    :raise FwmValidationError: The description is invalid (an empty one too), or a checkpoint it lists wasn't
                               created.
    """
    try:
        requested = read_ckpt_desc(desc)
    except OSError as e:
        raise NotFoundError(f"cannot read the checkpoint description {desc}: {e}")
    except CkptDescError as e:
        raise FwmValidationError(f"invalid checkpoint description {desc}: {e}")
    if not os.path.isdir(checkpoint_dir):
        raise NotFoundError(f"{checkpoint_dir} is not a directory")
    missing = [(name, count) for name, count in requested
               if not os.path.isfile(os.path.join(checkpoint_dir, name))]
    if missing:
        raise FwmValidationError(
            f"these checkpoints of {desc} are not files of {checkpoint_dir}, so they weren't created (a count at or "
            f"after the program's exit creates none): " + ", ".join(f"{n} ({c})" for n, c in missing))
    return [(name, count, os.path.join(checkpoint_dir, name)) for name, count in requested]


def _simpoint_info(path: Optional[str], names: List[str]) -> Optional[SimpointInfo]:
    """The SimPoint information of the checkpoints being imported, which must name exactly these checkpoints."""
    if path is None:
        return None
    try:
        info = load_simpoint_info(path)
    except OSError as e:
        raise NotFoundError(f"cannot read the SimPoint information {path}: {e}")
    except (json.JSONDecodeError, pydantic.ValidationError) as e:
        raise FwmValidationError(f"invalid SimPoint information {path}: {e}")
    problems = []
    unknown = sorted(set(info.checkpoints) - set(names))
    if unknown:
        problems.append(f"it names checkpoints that aren't imported: {', '.join(unknown)}")
    missing = [n for n in names if n not in info.checkpoints]
    if missing:
        problems.append(f"it has no SimPoint for these checkpoints: {', '.join(missing)}")
    indexes = [c.index for c in info.checkpoints.values()]
    duplicates = sorted({i for i in indexes if indexes.count(i) > 1})
    if duplicates:
        problems.append(f"several checkpoints have the SimPoint index {', '.join(map(str, duplicates))}")
    if problems:
        raise FwmValidationError(f"the SimPoint information {path} doesn't match the checkpoints: " +
                                 "; ".join(problems))
    return info


def add_checkpoints(repo: Repo, trace_ref: str, desc: str, checkpoint_dir: str, simpoint_info: Optional[str] = None,
                    description: Optional[str] = None, exist_ok: bool = False,
                    sim_version: Optional[str] = None) -> List[DecoupledCheckpoint]:
    """
    Import the checkpoints created from a trace by a description file, in one transaction. Each is checked against the
    trace, so that it is known to load with it.

    :param desc: The description file the checkpoints were created from (+create-checkpoint). Each checkpoint it lists
                 must have been created, and the instruction count it was requested at is kept.
    :param checkpoint_dir: The directory spike created them in. Only the checkpoints of the description are imported.
    :param simpoint_info: A JSON file of SimPoint information (see data_loader.simpoint_info) naming exactly these
                          checkpoints. Without it, the checkpoints have no SimPoint data.
    :param exist_ok: If the trace has a checkpoint of a name with the same content, keep it instead of failing.
    :param sim_version: The `spike --version` output of the simulator that created them, for provenance.
    :return: The checkpoints, in the order of the description.
    :raise AlreadyExistsError: The trace has a checkpoint of one of the names.
    """
    trace: DecoupledTrace = resolve_trace(trace_ref)
    requested = _requested_checkpoints(desc, checkpoint_dir)
    names = [name for name, _, _ in requested]
    info = _simpoint_info(simpoint_info, names)

    result: Dict[str, DecoupledCheckpoint] = {}
    todo = []
    for n, count, file in requested:
        existing = DecoupledCheckpoint.get_or_none((DecoupledCheckpoint.trace == trace) &
                                                   (DecoupledCheckpoint.name == n))
        if existing is not None:
            result[n] = _existing_checkpoint(existing, None, file, exist_ok)
        else:
            todo.append((file, n, count))

    with repo.staging() as staged:
        os.mkdir(staged)
        new = []
        for i, (file, n, count) in enumerate(todo):
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
            simpoint = info.checkpoints[n] if info is not None else None
            new.append(_NewCheckpoint(
                file=file, name=n, staged_dir=staged_dir, header=header, csr_counts=csr_counts,
                content_sha256=content_sha256, simpoint_weight=simpoint.weight if simpoint else None,
                simpoint_index=simpoint.index if simpoint else None,
                simpoint_interval=info.interval if info else None, simpoint_max_k=info.maxK if info else None,
                requested_count=count))

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
                    simpoint_interval=c.simpoint_interval, simpoint_max_k=c.simpoint_max_k,
                    requested_count=c.requested_count, sim_version=sim_version, description=description)
                for hart, csr_count in zip(c.header.harts, c.csr_counts):
                    CheckpointHart.create(checkpoint=ckpt, hart_id=hart.hart_id,
                                          recording_sha256=hart.recording_sha256, cmd_index=hart.cmd_index,
                                          hart_state_version=hart.state_version, csr_count=csr_count)
                os.makedirs(final_dir, exist_ok=True)
                os.chmod(final_dir, 0o755)  # it holds the read-only checkpoints of the trace, but stays writable itself
                place(os.path.join(c.staged_dir, c.name), os.path.join(final_dir, c.name))
                result[c.name] = ckpt
    return [result[n] for n in names]


def verify_checkpoint(repo: Repo, ckpt: DecoupledCheckpoint) -> List[FailedDetail]:
    """
    Verify a checkpoint in the repository against its catalog, ignoring the write bits it lost on import.
    """
    file_problems, link_problems, dir_problems = verify_catalog(
        load_catalog(ckpt.file_catalog), repo.checkpoint_dir(ckpt.trace.uuid), perm_mask=REPO_PERM_MASK)
    return sorted(file_problems + link_problems + dir_problems)
