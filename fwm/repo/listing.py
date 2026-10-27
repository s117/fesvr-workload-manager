"""
`fwm list`: the names of a kind of entities, filtered by the entities they are related to.

Entities are related along the chains workload / sysroot - entry point - trace - checkpoint: an entry point runs a
workload in a sysroot, a trace records a run of an entry point, and a checkpoint is made from a trace. A filter names
entities of a kind by shell-style globs, and keeps the listed entities related to one of them, directly or through
others; a filter of the listed kind selects by name. The globs of one filter are alternatives; different filters must
all hold.

Keep this module's imports light: `fwm list` loads it (see agent guide §3).
"""
import re
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple, Union, cast

import peewee as pw

from fwm.database.schema import DecoupledCheckpoint, DecoupledTrace, NativeEntry, Sysroot, Workload

# The kinds, as filters name them (singular), and as `fwm list` lists them (plural)
KINDS = ("workload", "sysroot", "entry", "trace", "checkpoint")
PLURALS = {"workload": "workloads", "sysroot": "sysroots", "entry": "entries", "trace": "traces",
           "checkpoint": "checkpoints"}


Rows = Iterable[Tuple[Any, ...]]  # what a query's .tuples() yields (the peewee stubs don't say)


def natural_key(value: str) -> List[Union[int, str]]:
    """Sort key that orders the numbers in a name by value: "e.2" before "e.10"."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


class _Chain(NamedTuple):
    """Related entities, by name: a sysroot without entry points has only its sysroot."""
    workload: Optional[str]
    sysroot: str
    entry: Optional[str]
    trace: Optional[str]  # its UUID, as `fwm show` prints it
    checkpoint: Optional[str]  # its name


def _chains() -> List[_Chain]:
    """Every chain of related entities: one per checkpoint, trace without checkpoints, entry point without traces, and
    sysroot without entry points."""
    rows = cast(Rows, NativeEntry
                .select(Workload.name, Sysroot.name, NativeEntry.name, DecoupledTrace.uuid, DecoupledCheckpoint.name)
                .join(Workload).switch(NativeEntry).join(Sysroot)
                .switch(NativeEntry).join(DecoupledTrace, pw.JOIN.LEFT_OUTER)
                .join(DecoupledCheckpoint, pw.JOIN.LEFT_OUTER)
                .tuples())
    chains = [_Chain(workload, sysroot, entry, str(uuid.UUID(str(trace))) if trace is not None else None, ckpt)
              for workload, sysroot, entry, trace, ckpt in rows]
    used = {chain.sysroot for chain in chains}
    chains += [_Chain(None, name, None, None, None) for (name,) in cast(Rows, Sysroot.select(Sysroot.name).tuples())
               if name not in used]
    return chains


def _matches(kind: str, chain: _Chain, glob: str) -> bool:
    """Whether the entity of a kind in a chain matches a glob (a checkpoint's may be "<trace glob>:<name glob>")."""
    if kind == "checkpoint" and ":" in glob:
        trace_glob, name_glob = glob.split(":", 1)
        return (chain.trace is not None and chain.checkpoint is not None and fnmatchcase(chain.trace, trace_glob)
                and fnmatchcase(chain.checkpoint, name_glob))
    value = getattr(chain, kind)
    return value is not None and fnmatchcase(value, glob)


def _checkpoint_refs() -> Dict[Tuple[str, str], str]:
    """How `fwm show checkpoint` takes each checkpoint, by (trace UUID, name): its name when no other checkpoint has
    it, else <trace UUID>:<name>."""
    rows = cast(Rows, DecoupledCheckpoint.select(DecoupledTrace.uuid, DecoupledCheckpoint.name).join(DecoupledTrace)
                .tuples())
    pairs = [(str(uuid.UUID(str(trace))), name) for trace, name in rows]
    counts: Dict[str, int] = {}
    for _, name in pairs:
        counts[name] = counts.get(name, 0) + 1
    return {(trace, name): name if counts[name] == 1 else f"{trace}:{name}" for trace, name in pairs}


@dataclass
class Listing:
    # The names of the entities listed, naturally sorted
    names: List[str]
    # The globs that match no entity of their filter's kind, as (kind, glob)
    unmatched: List[Tuple[str, str]] = field(default_factory=list)


def list_names(kind: str, filters: Dict[str, Sequence[str]]) -> Listing:
    """
    The names of the entities of a kind related to an entity that each filter matches.

    :param kind: The kind listed: one of KINDS.
    :param filters: The globs of each filter, by kind (one of KINDS); a filter without globs is no filter.
    :return: The names: traces by UUID, and checkpoints as `fwm show checkpoint` takes them.
    """
    assert kind in KINDS and all(k in KINDS for k in filters)
    filters = {k: globs for k, globs in filters.items() if globs}
    chains = _chains()
    unmatched = [(k, glob) for k, globs in filters.items() for glob in globs
                 if not any(_matches(k, chain, glob) for chain in chains)]
    kept = [chain for chain in chains
            if all(any(_matches(k, chain, glob) for glob in globs) for k, globs in filters.items())]

    names: Set[str]
    if kind == "checkpoint":
        refs = _checkpoint_refs()
        names = {refs[(chain.trace, chain.checkpoint)] for chain in kept
                 if chain.trace is not None and chain.checkpoint is not None}
    else:
        names = {value for value in (getattr(chain, kind) for chain in kept) if value is not None}
    return Listing(sorted(names, key=natural_key), unmatched)
