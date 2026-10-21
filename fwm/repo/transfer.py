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
from fwm.repo.repo import Repo, check_name, copy_source, REPO_DB_FILE, REPO_PERM_MASK
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


def _read_entry(source: Repo, name: str, traces: bool, checkpoints: bool) -> Optional[_Unit]:
    """
    Read an entry point and what is copied with it, its sysroot included, in a read transaction of its own. Return
    None if the source has no such entry point.
    """
    # DEFERRED: a read takes no write lock, even on a connection opened for writing (which begins IMMEDIATE)
    with source.db.atomic("DEFERRED"):
        entry = NativeEntry.get_or_none(NativeEntry.name == name)
        if entry is None:
            return None
        return _Unit(sysroot=_read_sysroot(entry.sysroot), entry=_Entry(
            name=entry.name, workload=_fields(entry.workload), sysroot=entry.sysroot.name,
            row=_fields(entry, "workload", "sysroot"),
            traces=[_read_trace(t, checkpoints) for t in entry.traces.order_by(DecoupledTrace.recording_started)]
            if traces else []))


def _read_source(source: Repo, entry_names: Sequence[str], everything: bool, traces: bool,
                 checkpoints: bool) -> List[_Unit]:
    """
    Read what is copied from the source. Each entry point is read in a read transaction of its own, so that reading
    a large repository doesn't hold up its writers; it is copied in a transaction of its own too.

    :param everything: Every entry point, and every sysroot that has none, instead of entry_names.
    :raise NotFoundError: An entry point isn't in the source.
    """
    with source.db.bind_ctx(ALL_MODELS):
        if everything:
            with source.db.atomic("DEFERRED"):
                names = [e.name for e in NativeEntry.select(NativeEntry.name).order_by(NativeEntry.name)]
        else:
            names = list(dict.fromkeys(entry_names))
            with source.db.atomic("DEFERRED"):
                missing = [n for n in names if NativeEntry.get_or_none(NativeEntry.name == n) is None]
            if missing:
                raise NotFoundError(f"no entry point named {', '.join(repr(n) for n in missing)} in {source.path}")

        units = []
        for name in names:
            unit = _read_entry(source, name, traces, checkpoints)
            if unit is None:
                if everything:
                    continue  # removed since it was listed
                raise NotFoundError(f"entry point '{name}' was removed from {source.path}")
            units.append(unit)

        if everything:
            # the sysroots that no entry point read brings, listed only now: they include one whose entry points were
            # removed since they were listed
            used = {unit.sysroot.name for unit in units}
            with source.db.atomic("DEFERRED"):
                others = [s.name for s in Sysroot.select(Sysroot.name).order_by(Sysroot.name) if s.name not in used]
            for name in others:
                with source.db.atomic("DEFERRED"):
                    sysroot = Sysroot.get_or_none(Sysroot.name == name)
                    if sysroot is not None:  # else removed since it was listed
                        units.append(_Unit(sysroot=_read_sysroot(sysroot), entry=None))
    return units


def _check_source(source: Repo, units: List[_Unit]) -> None:
    """
    Check what was read from the source before anything is copied:
    - its names and catalog paths, which become paths in the destination, as an import checks them (a source database
      changed by hand could otherwise make the copy write outside the destination);
    - that the entry points of a sysroot saw the same sysroot: each was read in a transaction of its own, and the
      sysroot may have been replaced in between. They then share one copy of it.

    :raise FwmValidationError: A name or path is invalid, or a sysroot changed while it was read.
    """
    def check_paths(catalog: FileCatalog, what: str) -> None:
        for path in sorted({f.path for f in catalog.files} | {link.path for link in catalog.links} |
                           {d.path for d in catalog.dirs}):
            if os.path.isabs(path) or any(part in ("", ".", "..") for part in path.split("/")):
                raise FwmValidationError(f"{what} of {source.path} has an invalid path in its catalog: {path!r}")

    sysroots: Dict[str, _Sysroot] = {}
    try:
        for unit in units:
            name = unit.sysroot.name
            if name not in sysroots:
                check_name("sysroot", name)
                check_paths(unit.sysroot.catalog, f"sysroot '{name}'")
                sysroots[name] = unit.sysroot
            elif catalog_differences(sysroots[name].catalog, unit.sysroot.catalog):
                raise FwmValidationError(f"sysroot '{name}' of {source.path} changed while it was read: copy again")
            unit.sysroot = sysroots[name]
            if unit.entry is None:
                continue
            check_name("entry point", unit.entry.name)
            check_name("workload", unit.entry.workload["name"])
            for trace in unit.entry.traces:
                check_paths(trace.catalog, f"trace {trace.uuid}")
                for ckpt in trace.checkpoints:
                    check_name("checkpoint", ckpt.name)
                    check_paths(ckpt.catalog, f"checkpoint {trace.uuid}:{ckpt.name}")
    except FwmValidationError as e:
        if source.path in str(e):
            raise
        raise FwmValidationError(f"{source.path}: {e}")


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


# The differences between a sysroot of the source and the destination's of the same name, by that name and the
# destination's catalog: rows never change, and _check_source() made sure the source has one sysroot of each name
_Compared = Dict[Tuple[str, int], List[str]]


def _plan(unit: _Unit, compared: _Compared) -> _Plan:
    """Compare a unit with the destination, the repository the models are bound to."""
    plan = _Plan()
    sysroot = unit.sysroot
    existing_sysroot = Sysroot.get_or_none(Sysroot.name == sysroot.name)
    if existing_sysroot is None:
        plan.sysroot = sysroot
        plan.items.append(Item(ADDED, "sysroot", sysroot.name))
    else:
        key = (sysroot.name, existing_sysroot.__data__["file_catalog"])  # (its id, without a query)
        if key not in compared:
            compared[key] = catalog_differences(sysroot.catalog, load_catalog(existing_sysroot.file_catalog))
        differences = compared[key]
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


def _tree_paths(tree: str) -> List[str]:
    """Every file, link and directory under tree, relative to it."""
    paths = []
    for dirpath, dirnames, filenames in os.walk(tree):
        paths += [os.path.relpath(os.path.join(dirpath, name), tree) for name in dirnames + filenames]
    return paths


def _stage(dest: Repo, stack: contextlib.ExitStack, source_path: str, catalog: FileCatalog, what: str,
           file_name: Optional[str] = None) -> str:
    """
    Copy a tree (or, with file_name, the file source_path into a folder, as file_name) into the destination's staging
    area, and check that the copy is what its catalog describes, and nothing else. Return the staged path.
    """
    staged: str = stack.enter_context(dest.staging())
    if file_name is None:
        copy_source(source_path, staged)
    else:
        os.mkdir(staged)
        copy_source(source_path, os.path.join(staged, file_name))
    file_problems, link_problems, dir_problems = verify_catalog(catalog, staged, perm_mask=REPO_PERM_MASK)
    listed = {f.path for f in catalog.files} | {link.path for link in catalog.links} | {d.path for d in catalog.dirs}
    problems = sorted(file_problems + link_problems + dir_problems +
                      [(path, "not in the catalog") for path in _tree_paths(staged) if path not in listed])
    if problems:
        raise FwmValidationError(f"the copy of {what} from {source_path} doesn't match its catalog:\n" +
                                 "\n".join(f"  {path}: {reason}" for path, reason in problems))
    set_tree_writable(staged)  # to move it to another parent
    return staged


def _copy_unit(source: Repo, dest: Repo, unit: _Unit, compared: _Compared) -> List[Item]:
    """Copy what the destination lacks of a unit, in one transaction. Return its items."""
    plan = _plan(unit, compared)
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

        plan = _commit(dest, unit, staged, compared)
    for warning in plan.warnings:
        Logger.get_logger().warning(warning)
    return plan.items


def _commit(dest: Repo, unit: _Unit, staged: Dict[str, str], compared: _Compared) -> _Plan:
    """
    Add the rows of what the destination lacks of a unit, and place its staged files, in one transaction. Return the
    plan committed.
    """
    created_dirs: List[str] = []  # checkpoint folders of new traces, which a failure must not leave behind
    try:
        with dest.importing() as place:
            # another command may have changed the destination meanwhile
            plan = _plan(unit, compared)
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
    if _same_repo(source.path, dest.path):
        raise RepoUnusableError(f"cannot copy {source.path} into itself")
    units = _read_source(source, entry_names, everything, traces, traces and checkpoints)
    _check_source(source, units)
    result = TransferResult()
    compared: _Compared = {}
    with dest.bound():
        # (a sysroot several entry points share conflicts once)
        conflicts = list(dict.fromkeys(c for unit in units for c in _plan(unit, compared).conflicts))
        if conflicts:
            raise AlreadyExistsError(f"{len(conflicts)} conflict(s) with {dest.path}, nothing was copied:\n" +
                                     "\n".join(f"  {c}" for c in conflicts))
        reported = set()
        for unit in units:
            for item in _copy_unit(source, dest, unit, compared):
                if (item.kind, item.id) not in reported:
                    reported.add((item.kind, item.id))
                    result.items.append(item)
                    report(item)
    return result


def _same_repo(a: str, b: str) -> bool:
    """Whether two paths are of the same repository: its database is the same file, even through a bind mount."""
    try:
        return os.path.samefile(os.path.join(a, REPO_DB_FILE), os.path.join(b, REPO_DB_FILE))
    except OSError:  # not both repositories
        return os.path.realpath(a) == os.path.realpath(b)


@contextlib.contextmanager
def _other_repo(path: str, this: Repo, readonly: bool) -> Iterator[Repo]:
    """The repository at path, opened beside this one without binding the models to it."""
    if _same_repo(path, this.path):
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
