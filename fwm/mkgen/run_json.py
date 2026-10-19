"""
The description of a run directory (run.json): what the run uses, so that its results can be decoded without the
repository.

Its sections are the views of `fwm show --json`, as they were when the run directory was generated. The lists of other
items in those views (a sysroot's entry points, an entry point's traces, a trace's checkpoints) are left out: they
describe the repository, not the run.
"""
from typing import Any, Dict, List, Optional, Tuple

from fwm.database.schema import NativeEntry, DecoupledTrace, DecoupledCheckpoint
from fwm.repo import query
from fwm.repo.repo import Repo

RUN_JSON = "run.json"


def _without(view: query.View, *keys: str) -> query.View:
    return {k: v for k, v in view.items() if k not in keys}


def describe_run(repo: Repo, entry: NativeEntry, trace: Optional[DecoupledTrace] = None,
                 ckpt: Optional[DecoupledCheckpoint] = None,
                 requested: Optional[List[Tuple[str, int]]] = None) -> Dict[str, Any]:
    """
    The sections of run.json about what the run uses.

    :param entry: The entry point the run records, or that recorded its trace.
    :param trace: The trace it replays, if any.
    :param ckpt: The checkpoint it loads, if any.
    :param requested: The (name, instruction count) of each checkpoint it creates, if any.
    """
    sections: Dict[str, Any] = {
        "sysroot": _without(query.sysroot_view(repo, entry.sysroot), "entries"),
        "workload": _without(query.workload_view(entry.workload), "entries"),
        # not what the repository holds besides: it changes, and the run doesn't use it
        "entry": _without(query.entry_view(entry), "traces", "num_checkpoints"),
    }
    if trace is not None:
        sections["trace"] = _without(query.trace_view(repo, trace), "checkpoints")
    if ckpt is not None:
        sections["checkpoint"] = query.checkpoint_view(repo, ckpt)
    if requested is not None:
        sections["requested_checkpoints"] = [{"name": name, "count": count} for name, count in requested]
    return sections
