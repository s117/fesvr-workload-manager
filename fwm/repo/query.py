"""
Plain views of the repository content, as dicts of JSON-compatible values, for `fwm show`.
"""
import json
from datetime import datetime
from typing import Any, Dict, Optional

import peewee as pw

from fwm.data_loader.sim_version import parse_sim_version, SimVersionError
from fwm.database.schema import (
    Workload, Sysroot, NativeEntry, DecoupledTrace, DecoupledCheckpoint, CatalogFile, CatalogLink, CatalogDir
)
from fwm.repo.lookup import checkpoint_harts, entry_metadata, trace_harts
from fwm.repo.repo import Repo

View = Dict[str, Any]


def _time(t: Optional[datetime]) -> Optional[str]:
    return t.isoformat(timespec="seconds") if t is not None else None


def sim_version_view(text: Optional[str]) -> Optional[View]:
    """The `spike --version` output kept for provenance: its build information, by component, and its raw text."""
    if text is None:
        return None
    try:
        builds: Any = parse_sim_version(text)
    except SimVersionError:
        builds = None
    return {"builds": builds, "raw": text}


def workload_view(workload: Workload) -> View:
    return {
        "name": workload.name,
        "creation_time": _time(workload.creation_time),
        "entries": sorted(e.name for e in workload.entries),
    }


def sysroot_view(repo: Repo, sysroot: Sysroot) -> View:
    catalog = sysroot.file_catalog
    size = CatalogFile.select(pw.fn.COALESCE(pw.fn.SUM(CatalogFile.actual_size), 0)).where(
        CatalogFile.catalog == catalog).scalar()
    return {
        "name": sysroot.name,
        "description": sysroot.description,
        "creation_time": _time(sysroot.creation_time),
        "path": repo.sysroot_dir(sysroot.name),
        "num_files": CatalogFile.select().where(CatalogFile.catalog == catalog).count(),
        "num_links": CatalogLink.select().where(CatalogLink.catalog == catalog).count(),
        "num_dirs": CatalogDir.select().where(CatalogDir.catalog == catalog).count(),
        "size_bytes": size,
        "entries": sorted(e.name for e in sysroot.entries),
    }


def entry_view(entry: NativeEntry) -> View:
    return {
        "name": entry.name,
        "workload": entry.workload.name,
        "sysroot": entry.sysroot.name,
        "description": entry.description,
        "creation_time": _time(entry.creation_time),
        "command": entry.command,
        "working_directory": entry.working_directory,
        "stdin_file": entry.stdin_file,
        "boot_elf": entry.boot_elf,
        "mem_mb": entry.mem_mb,
        "nprocs": entry.nprocs,
        "copy_spawn": entry.copy_spawn,
        "metadata": entry_metadata(entry),
        # each trace with its checkpoints' names (unique in their trace), ordered as trace_view orders them
        "traces": [{"uuid": str(t.uuid),
                    "checkpoints": [c.name for c in t.checkpoints.order_by(DecoupledCheckpoint.name)]}
                   for t in entry.traces.order_by(DecoupledTrace.creation_time)],
    }


def trace_view(repo: Repo, trace: DecoupledTrace) -> View:
    return {
        "uuid": str(trace.uuid),
        "entry": trace.native_entry.name,
        "description": trace.description,
        "creation_time": _time(trace.creation_time),
        "path": repo.trace_dir(trace.uuid),
        "mem_mb": trace.mem_mb,
        "num_harts": trace.num_harts,
        "boot_elf_sha256": trace.boot_elf_sha256,
        "exit_status": trace.exit_status,
        "complete": trace.exit_status is not None,
        # how it was recorded: recording_provenance.json, as recorded
        "provenance": json.loads(trace.provenance),
        "fwm_version": trace.fwm_version,
        "harts": [{
            "hart_id": h.hart_id,
            "recording_sha256": h.recording_sha256,
            "num_commands": h.num_commands,
            "instret": h.instret,
        } for h in trace_harts(trace)],
        "checkpoints": [c.name for c in trace.checkpoints.order_by(DecoupledCheckpoint.name)],
    }


def checkpoint_view(repo: Repo, ckpt: DecoupledCheckpoint) -> View:
    return {
        "name": ckpt.name,
        "trace": str(ckpt.trace.uuid),
        "entry": ckpt.trace.native_entry.name,
        "description": ckpt.description,
        "creation_time": _time(ckpt.creation_time),
        "path": repo.checkpoint_path(ckpt.trace.uuid, ckpt.name),
        "format_version": ckpt.format_version,
        "content_sha256": ckpt.content_sha256,
        "simpoint_weight": ckpt.simpoint_weight,
        "simpoint_index": ckpt.simpoint_index,
        "simpoint_interval": ckpt.simpoint_interval,
        "simpoint_max_k": ckpt.simpoint_max_k,
        "requested_count": ckpt.requested_count,
        "validation": {
            "status": ckpt.validation_status,
            "time": _time(ckpt.validation_time),
            "detail": ckpt.validation_detail,
        } if ckpt.validation_status is not None else None,
        "sim_version": sim_version_view(ckpt.sim_version),
        "fwm_version": ckpt.fwm_version,
        "harts": [{
            "hart_id": h.hart_id,
            "recording_sha256": h.recording_sha256,
            "cmd_index": h.cmd_index,
            "hart_state_version": h.hart_state_version,
            "csr_count": h.csr_count,
        } for h in checkpoint_harts(ckpt)],
    }
