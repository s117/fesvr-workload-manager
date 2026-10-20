"""
Copying entry points, with everything they need, from one repository to another (fwm repo pull, push and subrepo).

An entry point brings its workload and its sysroot, and, unless they are left out, its traces and their checkpoints.
The rows are copied as they are: names, trace UUIDs, provenance, metadata, descriptions, creation times, SimPoint data,
validation results. Nothing is imported or judged again.

What the destination already has is kept, under the rules of `repo add --exist-ok`:
- a sysroot of the same name and content;
- an entry point of the same name, sysroot, workload and launch fields (with its own metadata and description);
- a trace of the entry point made of the same recording (with its own UUID: the checkpoints copied attach to it);
- a checkpoint of that trace with the same name and content (with its own validation result).
The same name with something else is a conflict, and then nothing is copied. Rows of the destination never change, so
copying the same entry points again adds nothing.

Each entry point is copied in a transaction of its own, like an import: its files are staged in the destination,
checked against their catalogs, and placed as its rows are committed. An interrupted copy keeps the entry points already
committed, and running it again copies the rest.
"""
import contextlib
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

import peewee as pw

from fwm.data_loader.file_catalog import FileCatalog
from fwm.database.catalog_store import load_catalog, save_catalog
from fwm.database.schema import (
    ALL_MODELS, CheckpointHart, DecoupledCheckpoint, DecoupledTrace, NativeEntry, Sysroot, TraceHart, Workload
)
from fwm.repo.errors import AlreadyExistsError, FwmValidationError, NotFoundError, RepoUnusableError
from fwm.repo.lookup import entry_metadata, trace_harts
from fwm.repo.repo import Repo, copy_source, REPO_PERM_MASK
from fwm.utils.file_catalog import catalog_differences, verify_catalog
from fwm.utils.fs import set_tree_writable
from fwm.utils.log import Logger

ADDED = "added"
PRESENT = "present"

# The fields of an entry point that say how it runs: two entry points of the same name are the same if these are
_LAUNCH_FIELDS = ("command", "working_directory", "stdin_file", "boot_elf", "mem_mb", "nprocs", "copy_spawn")


def _fields(row: pw.Model, *references: str) -> Dict[str, Any]:
    """A row's own values by field name, without its id and its references (given by name)."""
    return {name: row.__data__.get(name) for name in row._meta.fields  # type: ignore[attr-defined]
            if name != "id" and name not in references}


# --- what is copied, read from the source


@dataclass
class _Checkpoint:
    name: str
    source_trace: uuid.UUID  # the UUID of its trace in the source
    row: Dict[str, Any]
    harts: List[Dict[str, Any]]
    catalog: FileCatalog


@dataclass
class _Trace:
    uuid: uuid.UUID
    row: Dict[str, Any]
    harts: List[Dict[str, Any]]
    catalog: FileCatalog
    checkpoints: List[_Checkpoint]

    def recording_sha256s(self) -> List[str]:
        return [h["recording_sha256"] for h in self.harts]

    def instrets(self) -> List[Optional[int]]:
        return [h["instret"] for h in self.harts]


@dataclass
class _Sysroot:
    name: str
    row: Dict[str, Any]
    catalog: FileCatalog


@dataclass
class _Entry:
    name: str
    workload: Dict[str, Any]
    sysroot: str
    row: Dict[str, Any]
    traces: List[_Trace]


@dataclass
class _Unit:
    """What is copied in one transaction: an entry point with its sysroot, or a sysroot that has no entry point."""
    sysroot: _Sysroot
    entry: Optional[_Entry]

    def describe(self) -> str:
        return f"entry point '{self.entry.name}'" if self.entry else f"sysroot '{self.sysroot.name}'"


def _read_sysroot(sysroot: Sysroot) -> _Sysroot:
    return _Sysroot(name=sysroot.name, row=_fields(sysroot, "file_catalog"), catalog=load_catalog(sysroot.file_catalog))


def _read_trace(trace: DecoupledTrace, checkpoints: bool) -> _Trace:
    ckpts = []
    if checkpoints:
        for ckpt in trace.checkpoints.order_by(DecoupledCheckpoint.name):
            ckpts.append(_Checkpoint(
                name=ckpt.name, source_trace=trace.uuid, row=_fields(ckpt, "trace", "file_catalog"),
                harts=[_fields(h, "checkpoint") for h in ckpt.harts.order_by(CheckpointHart.hart_id)],
                catalog=load_catalog(ckpt.file_catalog)))
    return _Trace(uuid=trace.uuid, row=_fields(trace, "native_entry", "file_catalog"),
                  harts=[_fields(h, "trace") for h in trace_harts(trace)], catalog=load_catalog(trace.file_catalog),
                  checkpoints=ckpts)


def _read_source(source: Repo, entry_names: Sequence[str], everything: bool, traces: bool,
                 checkpoints: bool) -> List[_Unit]:
    """
    Read what is copied from the source, in one read transaction so that it is a consistent snapshot.

    :param everything: Every entry point, and every sysroot that has none, instead of entry_names.
    :raise NotFoundError: An entry point isn't in the source.
    """
    # DEFERRED: a read takes no write lock, even on a connection opened for writing (which begins IMMEDIATE)
    with source.db.bind_ctx(ALL_MODELS), source.db.atomic("DEFERRED"):
        if everything:
            entries = list(NativeEntry.select().order_by(NativeEntry.name))
        else:
            names = list(dict.fromkeys(entry_names))
            found = {name: NativeEntry.get_or_none(NativeEntry.name == name) for name in names}
            missing = [name for name in names if found[name] is None]
            if missing:
                raise NotFoundError(f"no entry point named {', '.join(repr(n) for n in missing)} in {source.path}")
            entries = [e for e in found.values() if e is not None]

        sysroots: Dict[str, _Sysroot] = {}
        units = []
        for entry in entries:
            sysroot_name = entry.sysroot.name
            if sysroot_name not in sysroots:
                sysroots[sysroot_name] = _read_sysroot(entry.sysroot)
            units.append(_Unit(sysroot=sysroots[sysroot_name], entry=_Entry(
                name=entry.name, workload=_fields(entry.workload), sysroot=sysroot_name,
                row=_fields(entry, "workload", "sysroot"),
                traces=[_read_trace(t, checkpoints) for t in entry.traces.order_by(DecoupledTrace.recording_started)]
                if traces else [])))
        if everything:
            for sysroot in Sysroot.select().order_by(Sysroot.name):
                if sysroot.name not in sysroots:
                    units.append(_Unit(sysroot=_read_sysroot(sysroot), entry=None))
    return units


# --- what the destination lacks


@dataclass
class Item:
    """An item copied (ADDED) or already in the destination (PRESENT), as reported: one line of the output."""
    status: str
    kind: str  # "sysroot", "entry", "trace" or "checkpoint"
    id: str

    def __str__(self) -> str:
        return f"{self.status} {self.kind} {self.id}"


@dataclass
class _Plan:
    """What the destination lacks of a unit, and its conflicts with what the destination has."""
    items: List[Item] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    sysroot: Optional[_Sysroot] = None
    entry: Optional[_Entry] = None
    traces: List[_Trace] = field(default_factory=list)
    checkpoints: List[Tuple[uuid.UUID, _Checkpoint]] = field(default_factory=list)  # (the trace's UUID, checkpoint)

    def staged_keys(self) -> List[str]:
        """The files to copy, by a key of each."""
        return ([f"sysroot {self.sysroot.name}"] if self.sysroot else []) + \
            [f"trace {t.uuid}" for t in self.traces] + [f"checkpoint {u}:{c.name}" for u, c in self.checkpoints]


def _plan_checkpoints(plan: _Plan, trace: _Trace, dest_trace: Optional[DecoupledTrace]) -> None:
    for ckpt in trace.checkpoints:
        trace_uuid = dest_trace.uuid if dest_trace is not None else trace.uuid
        ref = f"{trace_uuid}:{ckpt.name}"
        existing = None
        if dest_trace is not None:
            existing = DecoupledCheckpoint.get_or_none((DecoupledCheckpoint.trace == dest_trace) &
                                                       (DecoupledCheckpoint.name == ckpt.name))
        if existing is None:
            plan.checkpoints.append((trace_uuid, ckpt))
            plan.items.append(Item(ADDED, "checkpoint", ref))
        elif existing.content_sha256 != ckpt.row["content_sha256"]:
            plan.conflicts.append(f"checkpoint {ref} exists, with different content")
        else:
            plan.items.append(Item(PRESENT, "checkpoint", ref))


def _plan_traces(plan: _Plan, entry: _Entry, dest_entry: Optional[NativeEntry]) -> None:
    for trace in entry.traces:
        same = None
        if dest_entry is not None:
            same = next((t for t in dest_entry.traces
                         if [h.recording_sha256 for h in trace_harts(t)] == trace.recording_sha256s()), None)
        if same is not None:
            stored = [h.instret for h in trace_harts(same)]
            if stored != trace.instrets():
                plan.conflicts.append(
                    f"trace {trace.uuid}: its recording already is trace {same.uuid} of entry point '{entry.name}', "
                    f"with a different final state: instret {stored}, not {trace.instrets()}")
                continue
            plan.items.append(Item(PRESENT, "trace", str(same.uuid) if same.uuid == trace.uuid
                                   else f"{same.uuid} ({trace.uuid})"))
            _plan_checkpoints(plan, trace, same)
            continue
        other = DecoupledTrace.get_or_none(DecoupledTrace.uuid == trace.uuid)
        if other is not None:
            plan.conflicts.append(
                f"trace {trace.uuid} exists, of entry point '{other.native_entry.name}', with another recording")
            continue
        plan.traces.append(trace)
        plan.items.append(Item(ADDED, "trace", str(trace.uuid)))
        _plan_checkpoints(plan, trace, None)


def _plan(unit: _Unit) -> _Plan:
    """Compare a unit with the destination, the repository the models are bound to."""
    plan = _Plan()
    sysroot = unit.sysroot
    existing_sysroot = Sysroot.get_or_none(Sysroot.name == sysroot.name)
    if existing_sysroot is None:
        plan.sysroot = sysroot
        plan.items.append(Item(ADDED, "sysroot", sysroot.name))
    else:
        differences = catalog_differences(sysroot.catalog, load_catalog(existing_sysroot.file_catalog))
        if differences:
            plan.conflicts.append(f"sysroot '{sysroot.name}' exists, with different content: {len(differences)} "
                                  f"path(s) differ, e.g. {differences[0]}")
        else:
            plan.items.append(Item(PRESENT, "sysroot", sysroot.name))

    entry = unit.entry
    if entry is None:
        return plan
    existing = NativeEntry.get_or_none(NativeEntry.name == entry.name)
    if existing is None:
        plan.entry = entry
        plan.items.append(Item(ADDED, "entry", entry.name))
    else:
        stored = (existing.sysroot.name, existing.workload.name) + tuple(getattr(existing, f) for f in _LAUNCH_FIELDS)
        given = (entry.sysroot, entry.workload["name"]) + tuple(entry.row[f] for f in _LAUNCH_FIELDS)
        if stored != given:
            plan.conflicts.append(f"entry point '{entry.name}' exists, and differs from this one")
            return plan
        metadata = json.loads(entry.row["metadata"]) if entry.row["metadata"] is not None else None
        if entry_metadata(existing) != metadata:
            plan.warnings.append(f"entry point '{entry.name}' keeps its metadata, which differs from the one copied: "
                                 f"change it with `fwm repo set-metadata`")
        plan.items.append(Item(PRESENT, "entry", entry.name))
    _plan_traces(plan, entry, existing)
    return plan


# --- copying


def _stage(dest: Repo, stack: contextlib.ExitStack, source_path: str, catalog: FileCatalog, what: str,
           file_name: Optional[str] = None) -> str:
    """
    Copy a tree (or, with file_name, the file source_path into a folder, as file_name) into the destination's staging
    area, and check the copy against its catalog. Return the staged path.
    """
    staged: str = stack.enter_context(dest.staging())
    if file_name is None:
        copy_source(source_path, staged)
    else:
        os.mkdir(staged)
        copy_source(source_path, os.path.join(staged, file_name))
    file_problems, link_problems, dir_problems = verify_catalog(catalog, staged, perm_mask=REPO_PERM_MASK)
    problems = sorted(file_problems + link_problems + dir_problems)
    if problems:
        raise FwmValidationError(f"the copy of {what} from {source_path} doesn't match its catalog:\n" +
                                 "\n".join(f"  {path}: {reason}" for path, reason in problems))
    set_tree_writable(staged)  # to move it to another parent
    return staged


def _copy_unit(source: Repo, dest: Repo, unit: _Unit) -> List[Item]:
    """Copy what the destination lacks of a unit, in one transaction. Return its items."""
    plan = _plan(unit)
    if plan.conflicts:  # a command changed the destination since the check of every unit
        raise AlreadyExistsError(f"{unit.describe()} conflicts with {dest.path}:\n" +
                                 "\n".join(f"  {c}" for c in plan.conflicts))
    if not plan.staged_keys() and plan.entry is None:
        for warning in plan.warnings:
            Logger.get_logger().warning(warning)
        return plan.items

    with contextlib.ExitStack() as stack:
        staged: Dict[str, str] = {}
        if plan.sysroot is not None:
            staged[f"sysroot {plan.sysroot.name}"] = _stage(
                dest, stack, source.sysroot_dir(plan.sysroot.name), plan.sysroot.catalog,
                f"sysroot '{plan.sysroot.name}'")
        for trace in plan.traces:
            staged[f"trace {trace.uuid}"] = _stage(dest, stack, source.trace_dir(trace.uuid), trace.catalog,
                                                   f"trace {trace.uuid}")
        for trace_uuid, ckpt in plan.checkpoints:
            staged[f"checkpoint {trace_uuid}:{ckpt.name}"] = _stage(
                dest, stack, source.checkpoint_path(ckpt.source_trace, ckpt.name), ckpt.catalog,
                f"checkpoint {ckpt.source_trace}:{ckpt.name}", file_name=ckpt.name)

        plan = _commit(dest, unit, staged)
    for warning in plan.warnings:
        Logger.get_logger().warning(warning)
    return plan.items


def _commit(dest: Repo, unit: _Unit, staged: Dict[str, str]) -> _Plan:
    """
    Add the rows of what the destination lacks of a unit, and place its staged files, in one transaction. Return the
    plan committed.
    """
    created_dirs: List[str] = []  # checkpoint folders of new traces, which a failure must not leave behind
    try:
        with dest.importing() as place:
            # another command may have changed the destination meanwhile
            plan = _plan(unit)
            if plan.conflicts:
                raise AlreadyExistsError(f"{unit.describe()} conflicts with {dest.path}:\n" +
                                         "\n".join(f"  {c}" for c in plan.conflicts))
            if any(key not in staged for key in plan.staged_keys()):
                raise FwmValidationError(f"{dest.path} changed while {unit.describe()} was copied: run it again")

            if plan.sysroot is not None:
                Sysroot.create(**plan.sysroot.row, file_catalog=save_catalog(plan.sysroot.catalog))
                place(staged[f"sysroot {plan.sysroot.name}"], dest.sysroot_dir(plan.sysroot.name))
            if plan.entry is not None:
                workload_row = dict(plan.entry.workload)
                workload, _ = Workload.get_or_create(name=workload_row.pop("name"), defaults=workload_row)
                NativeEntry.create(**plan.entry.row, workload=workload,
                                   sysroot=Sysroot.get(Sysroot.name == plan.entry.sysroot))
            if unit.entry is not None and (plan.traces or plan.checkpoints):
                entry = NativeEntry.get(NativeEntry.name == unit.entry.name)
                for trace in plan.traces:
                    row = DecoupledTrace.create(**trace.row, native_entry=entry,
                                                file_catalog=save_catalog(trace.catalog))
                    for hart in trace.harts:
                        TraceHart.create(**hart, trace=row)
                    place(staged[f"trace {trace.uuid}"], dest.trace_dir(trace.uuid))
                for trace_uuid, ckpt in plan.checkpoints:
                    ckpt_row = DecoupledCheckpoint.create(
                        **ckpt.row, trace=DecoupledTrace.get(DecoupledTrace.uuid == trace_uuid),
                        file_catalog=save_catalog(ckpt.catalog))
                    for hart in ckpt.harts:
                        CheckpointHart.create(**hart, checkpoint=ckpt_row)
                    final_dir = dest.checkpoint_dir(trace_uuid)
                    if not os.path.isdir(final_dir):
                        os.mkdir(final_dir)
                        created_dirs.append(final_dir)
                    os.chmod(final_dir, 0o755)  # it holds the read-only checkpoints of the trace, but stays writable
                    place(os.path.join(staged[f"checkpoint {trace_uuid}:{ckpt.name}"], ckpt.name),
                          dest.checkpoint_path(trace_uuid, ckpt.name))
    except BaseException:
        for path in created_dirs:
            try:
                os.rmdir(path)  # emptied by the rollback of importing()
            except OSError:
                pass  # left for `fwm repo gc`
        raise
    return plan


@dataclass
class TransferResult:
    items: List[Item] = field(default_factory=list)

    def summary(self) -> str:
        """E.g. "added 1 sysroot, 2 entry points; present 1 sysroot"."""
        names = {"sysroot": "sysroot", "entry": "entry point", "trace": "trace", "checkpoint": "checkpoint"}
        parts = []
        for status in (ADDED, PRESENT):
            counts = [(sum(1 for i in self.items if i.status == status and i.kind == kind), name)
                      for kind, name in names.items()]
            counted = [f"{n} {name}{'' if n == 1 else 's'}" for n, name in counts if n]
            if counted:
                parts.append(f"{status} " + ", ".join(counted))
        return "; ".join(parts) or "nothing to copy"


def transfer(source: Repo, dest: Repo, entry_names: Sequence[str] = (), everything: bool = False,
             traces: bool = True, checkpoints: bool = True,
             report: Callable[[Item], None] = lambda item: None) -> TransferResult:
    """
    Copy entry points, with what they need, from source to dest (see the module's description).

    :param entry_names: The entry points of the source to copy.
    :param everything: Copy every entry point of the source, and every sysroot that has none, instead.
    :param traces: Copy the entry points' traces (and, with checkpoints, their checkpoints).
    :param report: Called with each item once its entry point is committed (or found present). An item shared by
                   several entry points, like their sysroot, is reported once.
    :raise RepoUnusableError: source and dest are the same repository.
    :raise NotFoundError: An entry point isn't in the source.
    :raise AlreadyExistsError: Something conflicts with the destination: then nothing is copied.
    :raise FwmValidationError: A copied file doesn't match its catalog.
    """
    if os.path.realpath(source.path) == os.path.realpath(dest.path):
        raise RepoUnusableError(f"cannot copy {source.path} into itself")
    units = _read_source(source, entry_names, everything, traces, traces and checkpoints)
    result = TransferResult()
    with dest.bound():
        # (a sysroot several entry points share conflicts once)
        conflicts = list(dict.fromkeys(c for unit in units for c in _plan(unit).conflicts))
        if conflicts:
            raise AlreadyExistsError(f"{len(conflicts)} conflict(s) with {dest.path}, nothing was copied:\n" +
                                     "\n".join(f"  {c}" for c in conflicts))
        reported = set()
        for unit in units:
            for item in _copy_unit(source, dest, unit):
                if (item.kind, item.id) not in reported:
                    reported.add((item.kind, item.id))
                    result.items.append(item)
                    report(item)
    return result


@contextlib.contextmanager
def _other_repo(path: str, this: Repo, readonly: bool) -> Iterator[Repo]:
    """The repository at path, opened beside this one without binding the models to it."""
    if os.path.realpath(path) == os.path.realpath(this.path):
        raise RepoUnusableError(f"cannot copy {this.path} into itself")
    other = Repo.open(path, readonly=readonly, bind=False)
    try:
        yield other
    finally:
        other.close()


def pull(dest: Repo, source_path: str, entry_names: Sequence[str] = (), **kwargs: Any) -> TransferResult:
    """Copy entry points of the repository at source_path into dest (see transfer())."""
    with _other_repo(source_path, dest, readonly=True) as source:
        return transfer(source, dest, entry_names, **kwargs)


def push(source: Repo, dest_path: str, entry_names: Sequence[str] = (), **kwargs: Any) -> TransferResult:
    """Copy entry points of source into the repository at dest_path (see transfer())."""
    with _other_repo(dest_path, source, readonly=False) as dest:
        return transfer(source, dest, entry_names, **kwargs)
