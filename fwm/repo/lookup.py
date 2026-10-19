"""
Finding things in the repository by name or reference.

These are plain database queries, used by the read-only commands. Keep this module's imports light: the read-only
commands load it, and every module they load costs file lookups on a shared filesystem.
"""
import json
import re
from typing import Any, Dict, List, Optional

import peewee as pw

from fwm.database.schema import CheckpointHart, DecoupledCheckpoint, DecoupledTrace, NativeEntry, Sysroot, TraceHart
from fwm.repo.errors import FwmValidationError, NotFoundError
from fwm.utils.target_path import normalize_target_path


def get_sysroot(name: str) -> Sysroot:
    sysroot = Sysroot.get_or_none(Sysroot.name == name)
    if sysroot is None:
        raise NotFoundError(f"no sysroot named '{name}'")
    return sysroot


def get_entry(name: str) -> NativeEntry:
    entry = NativeEntry.get_or_none(NativeEntry.name == name)
    if entry is None:
        raise NotFoundError(f"no entry point named '{name}'")
    return entry


def entry_metadata(entry: NativeEntry) -> Optional[Dict[str, Any]]:
    """The entry point's metadata, or None if it has none."""
    return json.loads(entry.metadata) if entry.metadata is not None else None


def entry_target_cwd(entry: NativeEntry) -> str:
    """The entry's working directory, as a normalized target path (FESVR resolves a relative one against "/")."""
    return normalize_target_path("/", entry.working_directory)


def entry_target_path(entry: NativeEntry, path: str) -> str:
    """A path of the entry (boot ELF, stdin file), as a normalized target path."""
    return normalize_target_path(entry_target_cwd(entry), path)


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
