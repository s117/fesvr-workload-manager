import json
import os
import re
import uuid
from typing import List, Optional

import pydantic

from fwm.data_loader.final_state import read_final_state
from fwm.data_loader.recording import read_recording, RecordingFormatError
from fwm.database.catalog_store import save_catalog, load_catalog
from fwm.database.schema import DecoupledTrace, TraceHart, NativeEntry
from fwm.repo.entry import get_entry, entry_target_path
from fwm.repo.errors import FwmValidationError, NotFoundError
from fwm.repo.repo import Repo, DIGEST_ALGO, REPO_PERM_MASK
from fwm.utils.file_catalog import create_catalog, verify_catalog, FailedDetail
from fwm.utils.fs import copy_tree, set_tree_readonly, set_tree_writable
from fwm.utils.log import Logger
from fwm.utils.target_path import target_path_to_sysroot_relative


def resolve_trace(ref: str) -> DecoupledTrace:
    """
    Find a trace by its UUID (a unique prefix is enough), or by the name of an entry point that has exactly one trace.
    """
    entry = NativeEntry.get_or_none(NativeEntry.name == ref)
    if entry is not None:
        traces = list(entry.traces)
        if len(traces) == 1:
            return traces[0]
        if not traces:
            raise NotFoundError(f"entry point '{ref}' has no trace")
        raise FwmValidationError(
            f"entry point '{ref}' has {len(traces)} traces, give the UUID of one: " +
            ", ".join(str(t.uuid) for t in traces))

    prefix = ref.lower().replace("-", "")
    if not re.fullmatch(r"[0-9a-f]{1,32}", prefix):
        raise NotFoundError(f"no trace or entry point '{ref}'")
    matches = [t for t in DecoupledTrace.select() if t.uuid.hex.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise NotFoundError(f"no trace or entry point '{ref}'")
    raise FwmValidationError(f"'{ref}' matches {len(matches)} traces: " + ", ".join(str(t.uuid) for t in matches))


def trace_harts(trace: DecoupledTrace) -> List[TraceHart]:
    return list(trace.harts.order_by(TraceHart.hart_id))


def add_trace(repo: Repo, entry_name: str, recording_dir: str, final_state: Optional[str] = None,
              description: Optional[str] = None) -> DecoupledTrace:
    """
    Import a device traffic recording of an entry point's run as a trace.

    :param final_state: The +final-state-dump of the recording run, which gives the instret of each HART.
    """
    entry = get_entry(entry_name)
    if not os.path.isdir(recording_dir):
        raise NotFoundError(f"{recording_dir} is not a directory")
    try:
        recording = read_recording(recording_dir)
    except (RecordingFormatError, OSError, EOFError) as e:
        raise FwmValidationError(f"invalid recording {recording_dir}: {e}")
    spec = recording.target_spec

    if spec.mem_mb != entry.mem_mb:
        raise FwmValidationError(
            f"the recording is of a {spec.mem_mb} MB system, but entry point '{entry.name}' needs {entry.mem_mb} MB")
    if spec.num_harts != entry.nprocs:
        raise FwmValidationError(
            f"the recording is of a {spec.num_harts}-HART system, but entry point '{entry.name}' runs on "
            f"{entry.nprocs}")

    boot_elf = entry_target_path(entry, entry.boot_elf)
    boot_elf_rel = target_path_to_sysroot_relative(boot_elf)
    sysroot_catalog = load_catalog(entry.sysroot.file_catalog)
    assert sysroot_catalog.digest_algo == DIGEST_ALGO
    boot_elf_digest = next((f.digest.lower() for f in sysroot_catalog.files if f.path == boot_elf_rel), None)
    if boot_elf_digest != spec.boot_elf_sha256:
        raise FwmValidationError(
            f"the recording booted an ELF of SHA256 {spec.boot_elf_sha256}, but the boot ELF {boot_elf} of entry "
            f"point '{entry.name}' in sysroot '{entry.sysroot.name}' has SHA256 {boot_elf_digest}")

    instrets: Optional[List[int]] = None
    if final_state is not None:
        try:
            instrets = read_final_state(final_state)
        except (OSError, json.JSONDecodeError, pydantic.ValidationError) as e:
            raise FwmValidationError(f"invalid final state {final_state}: {e}")
        if len(instrets) != spec.num_harts:
            raise FwmValidationError(
                f"the final state {final_state} has {len(instrets)} HART(s), but the recording has {spec.num_harts}")

    log = Logger.get_logger()
    if recording.exit_status is None:
        log.warning(f"the recording {recording_dir} is truncated: the program never exited (e.g. the run was stopped "
                    f"by -e). It can't be replayed past its last command.")
    elif recording.exit_status != 0:
        log.warning(f"the program of the recording {recording_dir} exited with status {recording.exit_status}")

    trace_uuid = uuid.uuid4()
    with repo.staging() as staged:
        copy_tree(recording_dir, staged)
        catalog = create_catalog(DIGEST_ALGO, staged)
        final = repo.trace_dir(trace_uuid)
        set_tree_writable(staged)
        with repo.db.atomic():
            trace = DecoupledTrace.create(
                uuid=trace_uuid, native_entry=entry, file_catalog=save_catalog(catalog), mem_mb=spec.mem_mb,
                num_harts=spec.num_harts, boot_elf_sha256=spec.boot_elf_sha256, exit_status=recording.exit_status,
                description=description)
            for hart in recording.harts:
                TraceHart.create(trace=trace, hart_id=hart.hart_id, recording_sha256=hart.recording_sha256,
                                 num_commands=hart.num_commands,
                                 instret=instrets[hart.hart_id] if instrets is not None else None)
            repo.move_into_place(staged, final)
        set_tree_readonly(final)
    return trace


def verify_trace(repo: Repo, trace: DecoupledTrace) -> List[FailedDetail]:
    """
    Verify a trace in the repository against its catalog, ignoring the write bits it lost on import.
    """
    file_problems, link_problems, dir_problems = verify_catalog(
        load_catalog(trace.file_catalog), repo.trace_dir(trace.uuid), perm_mask=REPO_PERM_MASK)
    return sorted(file_problems + link_problems + dir_problems)
