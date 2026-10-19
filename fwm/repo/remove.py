from typing import Callable

from fwm.database.catalog_store import delete_catalog
from fwm.database.schema import Sysroot, NativeEntry, DecoupledTrace, DecoupledCheckpoint
from fwm.repo.lookup import get_entry, get_sysroot, resolve_checkpoint, resolve_trace
from fwm.repo.errors import FwmValidationError
from fwm.repo.repo import Repo

# Schedules a path to be deleted once the removal is committed (see Repo.removal())
Remover = Callable[[str], None]


def _remove_checkpoint(repo: Repo, ckpt: DecoupledCheckpoint, remove: Remover) -> None:
    catalog = ckpt.file_catalog
    ckpt.delete_instance()  # its harts cascade
    delete_catalog(catalog)
    remove(repo.checkpoint_path(ckpt.trace.uuid, ckpt.name))


def _remove_trace(repo: Repo, trace: DecoupledTrace, recursive: bool, remove: Remover) -> None:
    checkpoints = list(trace.checkpoints)
    if checkpoints and not recursive:
        raise FwmValidationError(
            f"trace {trace.uuid} has {len(checkpoints)} checkpoint(s): " +
            ", ".join(c.name for c in checkpoints) + "; remove them first, or use --recursive")
    for ckpt in checkpoints:
        _remove_checkpoint(repo, ckpt, remove)
    remove(repo.checkpoint_dir(trace.uuid))
    catalog = trace.file_catalog
    trace.delete_instance()  # its harts cascade
    delete_catalog(catalog)
    remove(repo.trace_dir(trace.uuid))


def _remove_entry(repo: Repo, entry: NativeEntry, recursive: bool, remove: Remover) -> None:
    traces = list(entry.traces)
    if traces and not recursive:
        raise FwmValidationError(
            f"entry point '{entry.name}' has {len(traces)} trace(s): " + ", ".join(str(t.uuid) for t in traces) +
            "; remove them first, or use --recursive")
    for trace in traces:
        _remove_trace(repo, trace, recursive, remove)
    workload = entry.workload
    entry.delete_instance()
    # a workload has no content of its own: it goes with its last entry point
    if workload.entries.count() == 0:
        workload.delete_instance()


def remove_checkpoint(repo: Repo, ref: str) -> None:
    ckpt = resolve_checkpoint(ref)
    with repo.removal() as remove:
        _remove_checkpoint(repo, ckpt, remove)


def remove_trace(repo: Repo, ref: str, recursive: bool = False) -> None:
    trace = resolve_trace(ref)
    with repo.removal() as remove:
        _remove_trace(repo, trace, recursive, remove)


def remove_entry(repo: Repo, name: str, recursive: bool = False) -> None:
    entry = get_entry(name)
    with repo.removal() as remove:
        _remove_entry(repo, entry, recursive, remove)


def remove_sysroot(repo: Repo, name: str, recursive: bool = False) -> None:
    sysroot: Sysroot = get_sysroot(name)
    with repo.removal() as remove:
        entries = list(sysroot.entries)
        if entries and not recursive:
            raise FwmValidationError(
                f"sysroot '{name}' has {len(entries)} entry point(s): " + ", ".join(e.name for e in entries) +
                "; remove them first, or use --recursive")
        for entry in entries:
            _remove_entry(repo, entry, recursive, remove)
        catalog = sysroot.file_catalog
        sysroot.delete_instance()
        delete_catalog(catalog)
        remove(repo.sysroot_dir(name))
