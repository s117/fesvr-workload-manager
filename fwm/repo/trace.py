import os
import re
import uuid
from typing import List, Optional, Tuple

import peewee as pw

from fwm.data_loader.final_state import read_final_state, FinalState, FinalStateError, FinishReason
from fwm.data_loader.recording import (
    read_recording, read_recording_sha256, read_target_spec, Recording, RecordingFormatError
)
from fwm.database.catalog_store import save_catalog, load_catalog
from fwm.database.schema import CatalogFile, DecoupledTrace, TraceHart, NativeEntry
from fwm.repo.entry import get_entry, entry_target_path
from fwm.repo.errors import AlreadyExistsError, FwmValidationError, NotFoundError
from fwm.repo.repo import Repo, catalog_of, copy_source, DIGEST_ALGO, REPO_PERM_MASK
from fwm.utils.file_catalog import verify_catalog, FailedDetail
from fwm.utils.fs import set_tree_writable
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
    # the UUIDs are stored as 32 hex digits
    matches = list(DecoupledTrace.select().where(pw.fn.substr(DecoupledTrace.uuid, 1, len(prefix)) == prefix))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise NotFoundError(f"no trace or entry point '{ref}'")
    raise FwmValidationError(f"'{ref}' matches {len(matches)} traces: " + ", ".join(str(t.uuid) for t in matches))


def trace_harts(trace: DecoupledTrace) -> List[TraceHart]:
    return list(trace.harts.order_by(TraceHart.hart_id))


# What to do with a record run that didn't end with the program's exit
_NOT_AN_EXIT = {
    FinishReason.INTERRUPTED: "record it again (with more time, if it was a time limit)",
    FinishReason.INSTRET_LIMIT: "its recording is truncated: import it without --final-state, or record it again without -e",
    FinishReason.ERROR: "record it again once that is fixed",
    FinishReason.UNFINISHED: "record it again",
}


def _read_final_state(final_state: Optional[str]) -> Optional[FinalState]:
    """The +final-state-dump of a record run, if one is given: the run must have ended with the program's exit."""
    if final_state is None:
        return None
    try:
        state = read_final_state(final_state)
    except FinalStateError as e:
        raise FwmValidationError(str(e))
    if state.finish_reason != FinishReason.TARGET_EXIT:
        advice = _NOT_AN_EXIT.get(state.finish_reason, "it isn't the final state of a record run")
        raise FwmValidationError(
            f"the run of the final state {final_state} didn't end with the program's exit: {state.describe()}; {advice}")
    return state


def _judge_recording(recording: Recording, state: Optional[FinalState], recording_dir: str,
                     final_state: Optional[str]) -> None:
    """Check that a recording and the final state of its run (which says the program exited) agree."""
    if state is None:
        return
    spec = recording.target_spec
    instrets = state.instrets or []
    if len(instrets) != spec.num_harts:
        raise FwmValidationError(
            f"the final state {final_state} has {len(instrets)} HART(s), but the recording has {spec.num_harts}")
    if recording.exit_status is None:
        raise FwmValidationError(
            f"the final state {final_state} says the program exited, but the recording {recording_dir} is truncated: "
            f"they aren't of the same run")
    if recording.exit_status != (state.exit_code or 0) & 0xff:
        raise FwmValidationError(
            f"the final state {final_state} says the program exited with code {state.exit_code}, but the recording "
            f"{recording_dir} with status {recording.exit_status}: they aren't of the same run")


def check_recording(recording_dir: str, final_state: Optional[str] = None) -> Tuple[Recording, Optional[FinalState]]:
    """
    Check that a recording is complete and consistent, as `fwm repo add trace` would, without a repository: e.g. on the
    node that ran it, so that a run cut short is caught there.

    :param final_state: The +final-state-dump of the recording run.
    :return: The recording, and the final state, if one is given.
    :raise FwmValidationError: The recording is malformed or truncated (the program never exited), the final state
                               doesn't say the program exited, or the two disagree.
    """
    if not os.path.isdir(recording_dir):
        raise NotFoundError(f"{recording_dir} is not a directory")
    state = _read_final_state(final_state)
    try:
        recording = read_recording(recording_dir)
    except (RecordingFormatError, OSError, EOFError) as e:
        raise FwmValidationError(f"invalid recording {recording_dir}: {e}")
    _judge_recording(recording, state, recording_dir, final_state)
    if recording.exit_status is None:
        raise FwmValidationError(
            f"the recording {recording_dir} is truncated: the program never exited (e.g. the run was stopped by -e, or "
            f"by an error of the simulator)")
    return recording, state


def _source_recording_sha256s(recording_dir: str) -> Optional[List[str]]:
    """The per-HART SHA256s of a recording, read cheaply from its .sha256 files; None if they can't be read (the full
    check of the recording then reports why)."""
    try:
        spec = read_target_spec(recording_dir)
        return [read_recording_sha256(recording_dir, hart) for hart in range(spec.num_harts)]
    except Exception:
        return None


def _trace_with_recording(entry: NativeEntry, sha256s: Optional[List[str]]) -> Optional[DecoupledTrace]:
    """The trace of an entry point made of this very recording (the same SHA256 for every HART), if any."""
    if sha256s is None:
        return None
    for trace in entry.traces:
        if [h.recording_sha256 for h in trace_harts(trace)] == sha256s:
            return trace
    return None


def _existing_trace(trace: DecoupledTrace, instrets: Optional[List[int]], exist_ok: bool) -> DecoupledTrace:
    """The answer to importing a recording that already is a trace: that trace with --exist-ok, else an error."""
    what = f"this recording already is trace {trace.uuid} of entry point '{trace.native_entry.name}'"
    if not exist_ok:
        raise AlreadyExistsError(what)
    stored = [h.instret for h in trace_harts(trace)]
    if instrets is not None and stored != instrets or instrets is None and any(i is not None for i in stored):
        raise AlreadyExistsError(f"{what}, with a different final state: instret {stored}")
    return trace


def add_trace(repo: Repo, entry_name: str, recording_dir: str, final_state: Optional[str] = None,
              description: Optional[str] = None, exist_ok: bool = False,
              sim_version: Optional[str] = None) -> DecoupledTrace:
    """
    Import a device traffic recording of an entry point's run as a trace.

    :param final_state: The +final-state-dump of the recording run, which gives the instret of each HART.
    :param exist_ok: If the recording already is a trace of the entry point, with the same final state, return it
                     instead of failing.
    :param sim_version: The `spike --version` output of the simulator that made the recording, for provenance.
    :raise AlreadyExistsError: The recording already is a trace of the entry point.
    """
    entry = get_entry(entry_name)
    if not os.path.isdir(recording_dir):
        raise NotFoundError(f"{recording_dir} is not a directory")
    state = _read_final_state(final_state)
    instrets = state.instrets if state is not None else None
    existing = _trace_with_recording(entry, _source_recording_sha256s(recording_dir))
    if existing is not None:
        return _existing_trace(existing, instrets, exist_ok)

    with repo.staging() as staged:
        copy_source(recording_dir, staged)
        # check the copy that is stored, not the source
        try:
            recording = read_recording(staged)
        except (RecordingFormatError, OSError, EOFError) as e:
            raise FwmValidationError(f"invalid recording {recording_dir}: {str(e).replace(staged, recording_dir)}")
        return _import_trace(repo, entry, recording_dir, staged, recording, final_state, state, description,
                             exist_ok, sim_version)


def _import_trace(repo: Repo, entry: NativeEntry, recording_dir: str, staged: str, recording: Recording,
                  final_state: Optional[str], state: Optional[FinalState], description: Optional[str],
                  exist_ok: bool, sim_version: Optional[str]) -> DecoupledTrace:
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
    assert entry.sysroot.file_catalog.digest_algo == DIGEST_ALGO
    boot_elf_file = CatalogFile.get_or_none((CatalogFile.catalog == entry.sysroot.file_catalog) &
                                            (CatalogFile.path == boot_elf_rel))
    boot_elf_digest = boot_elf_file.digest.lower() if boot_elf_file is not None else None
    if boot_elf_digest != spec.boot_elf_sha256:
        raise FwmValidationError(
            f"the recording booted an ELF of SHA256 {spec.boot_elf_sha256}, but the boot ELF {boot_elf} of entry "
            f"point '{entry.name}' in sysroot '{entry.sysroot.name}' has SHA256 {boot_elf_digest}")

    _judge_recording(recording, state, recording_dir, final_state)
    instrets = state.instrets if state is not None else None

    log = Logger.get_logger()
    if recording.exit_status is None:
        log.warning(f"the recording {recording_dir} is truncated: the program never exited (e.g. the run was stopped "
                    f"by -e). It can't be replayed past its last command.")
    elif recording.exit_status != 0:
        log.warning(f"the program of the recording {recording_dir} exited with status {recording.exit_status}")

    trace_uuid = uuid.uuid4()
    catalog = catalog_of(staged, recording_dir)
    set_tree_writable(staged)
    with repo.importing() as place:
        if NativeEntry.get_or_none(NativeEntry.name == entry.name) is None:
            raise NotFoundError(f"entry point '{entry.name}' was removed during the import")
        # another import of the same recording may have committed meanwhile
        existing = _trace_with_recording(entry, [h.recording_sha256 for h in recording.harts])
        if existing is not None:
            return _existing_trace(existing, instrets, exist_ok)
        trace = DecoupledTrace.create(
            uuid=trace_uuid, native_entry=entry, file_catalog=save_catalog(catalog), mem_mb=spec.mem_mb,
            num_harts=spec.num_harts, boot_elf_sha256=spec.boot_elf_sha256, exit_status=recording.exit_status,
            sim_version=sim_version, description=description)
        for hart in recording.harts:
            TraceHart.create(trace=trace, hart_id=hart.hart_id, recording_sha256=hart.recording_sha256,
                             num_commands=hart.num_commands,
                             instret=instrets[hart.hart_id] if instrets is not None else None)
        place(staged, repo.trace_dir(trace_uuid))
    return trace


def verify_trace(repo: Repo, trace: DecoupledTrace) -> List[FailedDetail]:
    """
    Verify a trace in the repository against its catalog, ignoring the write bits it lost on import.
    """
    file_problems, link_problems, dir_problems = verify_catalog(
        load_catalog(trace.file_catalog), repo.trace_dir(trace.uuid), perm_mask=REPO_PERM_MASK)
    return sorted(file_problems + link_problems + dir_problems)
