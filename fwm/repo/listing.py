"""
`fwm list`: the names of a kind of entities, filtered by the entities they are related to.

Entities are related along the chains workload / sysroot - entry point - trace - checkpoint: an entry point runs a
workload in a sysroot, a trace records a run of an entry point, and a checkpoint is made from a trace. A filter names
entities of a kind by shell-style globs, and keeps the listed entities related to one of them, directly or through
others; a filter of the listed kind selects by name. The globs of one filter are alternatives. Each filter keeps its
own set, and the result is the intersection of those sets: e.g. the workloads related to sysroot s1 and to entry
point c, which may be through different entry points.

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


def natural_order(values: Iterable[str]) -> List[str]:
    """Values in natural order; those natural_key makes equal (e.g. "e1" and "e01") in plain order, never at random."""
    return sorted(values, key=lambda value: (natural_key(value), value))


class _Chain(NamedTuple):
    """Related entities, by name: a sysroot without entry points has only its sysroot."""
    workload: Optional[str]
    sysroot: str
    entry: Optional[str]
    trace: Optional[str]  # its UUID, as `fwm show` prints it
    checkpoint: Optional[str]  # its name
    simpoint: bool = False  # the checkpoint has SimPoint information: `fwm mkgen from-simpoint` can simulate it


def _chains() -> List[_Chain]:
    """Every chain of related entities: one per checkpoint, trace without checkpoints, entry point without traces, and
    sysroot without entry points."""
    rows = cast(Rows, NativeEntry
                .select(Workload.name, Sysroot.name, NativeEntry.name, DecoupledTrace.uuid, DecoupledCheckpoint.name,
                        DecoupledCheckpoint.simpoint_interval)
                .join(Workload).switch(NativeEntry).join(Sysroot)
                .switch(NativeEntry).join(DecoupledTrace, pw.JOIN.LEFT_OUTER)
                .join(DecoupledCheckpoint, pw.JOIN.LEFT_OUTER)
                .tuples())
    chains = [_Chain(workload, sysroot, entry, str(uuid.UUID(str(trace))) if trace is not None else None, ckpt,
                     interval is not None)
              for workload, sysroot, entry, trace, ckpt, interval in rows]
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


def list_names(kind: str, filters: Dict[str, Sequence[str]], simpoint: bool = False) -> Listing:
    """
    The names of the entities of a kind that every filter keeps: those related to an entity it matches.

    :param kind: The kind listed: one of KINDS.
    :param filters: The globs of each filter, by kind (one of KINDS); a filter without globs is no filter.
    :param simpoint: One more filter: only what is related to a checkpoint with SimPoint information, the checkpoints
                     `fwm mkgen from-simpoint` can simulate.
    :return: The names: traces by UUID, and checkpoints as `fwm show checkpoint` takes them.
    """
    assert kind in KINDS and all(k in KINDS for k in filters)
    filters = {k: globs for k, globs in filters.items() if globs}
    chains = _chains()
    unmatched = [(k, glob) for k, globs in filters.items() for glob in globs
                 if not any(_matches(k, chain, glob) for chain in chains)]
    refs = _checkpoint_refs() if kind == "checkpoint" else {}

    def listed(chain: _Chain) -> Optional[str]:
        """The name of the chain's entity of the listed kind, if it has one."""
        if kind == "checkpoint":
            return refs[(chain.trace, chain.checkpoint)] if chain.trace and chain.checkpoint else None
        return getattr(chain, kind)

    def names_of(selected: Iterable[_Chain]) -> Set[str]:
        return {name for name in map(listed, selected) if name is not None}

    # each filter keeps the listed entities of the chains it matches; the result is what every filter keeps
    names = names_of(chains)
    for k, globs in filters.items():
        names &= names_of(chain for chain in chains if any(_matches(k, chain, glob) for glob in globs))
    if simpoint:
        names &= names_of(chain for chain in chains if chain.simpoint)
    return Listing(natural_order(names), unmatched)
