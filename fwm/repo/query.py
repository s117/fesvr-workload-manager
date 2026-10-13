"""
Plain views of the repository content, as dicts of JSON-compatible values, for `fwm list` and `fwm show`.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

import peewee as pw

from fwm.database.schema import (
    Workload, Sysroot, NativeEntry, DecoupledTrace, DecoupledCheckpoint, CatalogFile, CatalogLink, CatalogDir
)
from fwm.repo.checkpoint import checkpoint_harts
from fwm.repo.repo import Repo
from fwm.repo.trace import trace_harts

View = Dict[str, Any]


def _time(t: Optional[datetime]) -> Optional[str]:
    return t.isoformat(timespec="seconds") if t is not None else None


def workload_view(workload: Workload) -> View:
    return {
        "name": workload.name,
        "description": workload.description,
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
        "traces": [str(t.uuid) for t in entry.traces.order_by(DecoupledTrace.creation_time)],
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
        "harts": [{
            "hart_id": h.hart_id,
            "recording_sha256": h.recording_sha256,
            "cmd_index": h.cmd_index,
            "hart_state_version": h.hart_state_version,
            "csr_count": h.csr_count,
        } for h in checkpoint_harts(ckpt)],
    }


def list_workloads(name_filter: Optional[str] = None) -> List[View]:
    query = Workload.select().order_by(Workload.name)
    return [workload_view(w) for w in query if not name_filter or name_filter in w.name]


def list_sysroots(repo: Repo, name_filter: Optional[str] = None) -> List[View]:
    query = Sysroot.select().order_by(Sysroot.name)
    return [sysroot_view(repo, s) for s in query if not name_filter or name_filter in s.name]


def list_entries(name_filter: Optional[str] = None, workload: Optional[str] = None,
                 sysroot: Optional[str] = None) -> List[View]:
    query = NativeEntry.select().join(Workload).switch(NativeEntry).join(Sysroot).order_by(NativeEntry.name)
    if workload is not None:
        query = query.where(Workload.name == workload)
    if sysroot is not None:
        query = query.where(Sysroot.name == sysroot)
    return [entry_view(e) for e in query if not name_filter or name_filter in e.name]


def list_traces(repo: Repo, entry_filter: Optional[str] = None) -> List[View]:
    query = DecoupledTrace.select().join(NativeEntry).order_by(NativeEntry.name, DecoupledTrace.creation_time)
    return [trace_view(repo, t) for t in query if not entry_filter or entry_filter in t.native_entry.name]


def list_checkpoints(repo: Repo, name_filter: Optional[str] = None,
                     trace: Optional[DecoupledTrace] = None) -> List[View]:
    query = DecoupledCheckpoint.select().order_by(DecoupledCheckpoint.name)
    if trace is not None:
        query = query.where(DecoupledCheckpoint.trace == trace)
    return [checkpoint_view(repo, c) for c in query if not name_filter or name_filter in c.name]
