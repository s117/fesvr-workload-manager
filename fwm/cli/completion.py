"""
Shell completion of the names of repository content: sysroots, workloads, entry points, traces and checkpoints.

Click calls these while it parses the command line for completion, without running the command callbacks, so the
repository is opened here from the parsed --repo-path (or FWM_REPO_PATH). Completion must never fail loudly: without a
usable repository, or on any error, a callback offers nothing.
"""
import os
import re
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple, Union

import click
from click.shell_completion import CompletionItem

from fwm.database.schema import Sysroot, Workload, NativeEntry, DecoupledTrace, DecoupledCheckpoint
from fwm.repo.repo import Repo
from fwm.repo.lookup import resolve_trace

Candidates = Iterable[Tuple[str, Optional[str]]]  # (value, help)
Completer = Callable[[click.Context, click.Parameter, str], List[CompletionItem]]


def _natural_key(value: str) -> List[Union[int, str]]:
    """Sort key that orders the numbers in a name by value: "e.2" before "e.10"."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def _expand(path: Optional[str]) -> Optional[str]:
    # the shell expands a leading ~ when it runs a command, not in the words it passes to complete one
    return os.path.expanduser(path) if path else path


def _repo_path(ctx: click.Context) -> Optional[str]:
    """The repository of --repo-path (or FWM_REPO_PATH)."""
    return _expand(ctx.find_root().params.get("repo_path"))


def _source_path(ctx: click.Context) -> Optional[str]:
    """The repository of the SOURCE argument (fwm repo pull)."""
    return _expand(ctx.params.get("source"))


def _open_repo_quietly(path: Optional[str]) -> Optional[Repo]:
    if not path:
        return None
    try:
        return Repo.open(path, readonly=True)
    except Exception:
        return None


def _completer(candidates: Callable[[click.Context, str], Candidates],
               repo_path: Callable[[click.Context], Optional[str]] = _repo_path) -> Completer:
    """
    Make a click shell_complete callback that offers the candidates starting with the incomplete value, naturally
    sorted, from the repository given on the command line: by default --repo-path.
    """
    def complete(ctx: click.Context, param: click.Parameter, incomplete: str) -> List[CompletionItem]:
        repo = _open_repo_quietly(repo_path(ctx))
        if repo is None:
            return []
        try:
            items = {value: help_text for value, help_text in candidates(ctx, incomplete)
                     if value.startswith(incomplete)}
            return [CompletionItem(value, help=items[value]) for value in sorted(items, key=_natural_key)]
        except Exception:
            return []
        finally:
            repo.close()

    return complete


def _count(n: int, what: str) -> str:
    return f"{n} {what}{'' if n == 1 else 's'}"


def _sysroots(ctx: click.Context, incomplete: str) -> Candidates:
    for sysroot in Sysroot.select():
        yield sysroot.name, _count(sysroot.entries.count(), "entry point")


def _workloads(ctx: click.Context, incomplete: str) -> Candidates:
    for workload in Workload.select():
        yield workload.name, _count(workload.entries.count(), "entry point")


def _entries(ctx: click.Context, incomplete: str) -> Candidates:
    for entry in NativeEntry.select():
        yield entry.name, f"{entry.workload.name} @ {entry.sysroot.name}"


def _other_entries(ctx: click.Context, incomplete: str) -> Candidates:
    """The entry points not given yet to the ENTRIES argument (fwm repo pull, push and subrepo), unless --all is."""
    if ctx.params.get("everything"):
        return []
    given = set(ctx.params.get("entries") or ())
    return ((name, help_text) for name, help_text in _entries(ctx, incomplete) if name not in given)


def _trace_help(trace: DecoupledTrace) -> str:
    state = "complete" if trace.exit_status is not None else "truncated"
    return f"{trace.native_entry.name}, {state}, {_count(trace.checkpoints.count(), 'checkpoint')}"


def _traces(ctx: click.Context, incomplete: str) -> Candidates:
    """Trace UUIDs, and the entry points with exactly one trace, which name it too."""
    traces_of: Dict[str, List[DecoupledTrace]] = {}
    for trace in DecoupledTrace.select():
        traces_of.setdefault(trace.native_entry.name, []).append(trace)
        yield str(trace.uuid), _trace_help(trace)
    for entry_name, traces in traces_of.items():
        if len(traces) == 1:
            yield entry_name, f"its trace {traces[0].uuid}"


def _checkpoint_help(ckpt: DecoupledCheckpoint) -> str:
    weight = f", weight {ckpt.simpoint_weight}" if ckpt.simpoint_weight is not None else ""
    return f"{ckpt.trace.native_entry.name}{weight}"


def _checkpoint_refs(incomplete: str) -> Iterator[Tuple[str, DecoupledCheckpoint]]:
    """
    Checkpoints as resolve_checkpoint() accepts them, each once: by its name when the name is unique in the repository,
    otherwise as <trace UUID>:<name>.

    After a typed ':', offer the checkpoints of the trace before it (a UUID prefix or an entry point with one trace),
    keeping that prefix as typed.
    """
    if ":" in incomplete:
        trace_ref = incomplete.split(":", 1)[0]
        trace = resolve_trace(trace_ref)
        for ckpt in trace.checkpoints:
            yield f"{trace_ref}:{ckpt.name}", ckpt
        return

    by_name: Dict[str, List[DecoupledCheckpoint]] = {}
    for ckpt in DecoupledCheckpoint.select():
        by_name.setdefault(ckpt.name, []).append(ckpt)
    for name, ckpts in by_name.items():
        if len(ckpts) == 1:
            yield name, ckpts[0]
        else:
            for ckpt in ckpts:
                yield f"{ckpt.trace.uuid}:{name}", ckpt


def _checkpoints(ctx: click.Context, incomplete: str) -> Candidates:
    return ((ref, _checkpoint_help(ckpt)) for ref, ckpt in _checkpoint_refs(incomplete))


def _simpoint_checkpoints(ctx: click.Context, incomplete: str) -> Candidates:
    """The checkpoints that have SimPoint information (fwm mkgen from-simpoint)."""
    return ((ref, _checkpoint_help(ckpt)) for ref, ckpt in _checkpoint_refs(incomplete)
            if ckpt.simpoint_interval is not None)


_BY_KIND: Dict[str, Callable[[click.Context, str], Candidates]] = {
    "workload": _workloads,
    "sysroot": _sysroots,
    "entry": _entries,
    "trace": _traces,
    "checkpoint": _checkpoints,
}


def _by_kind(ctx: click.Context, incomplete: str) -> Candidates:
    """The ID of the kind given by the preceding KIND argument (show, repo compose remove, repo verify)."""
    candidates = _BY_KIND.get(ctx.params.get("kind") or "")
    return candidates(ctx, incomplete) if candidates else []


def _list_filter(ctx: click.Context, incomplete: str) -> Candidates:
    """The FILTER of `fwm list <kind>`: the names it matches (for traces, the names of their entry points)."""
    kind = ctx.params.get("kind") or "entries"
    if kind == "traces":
        return ((name, None) for name in {t.native_entry.name for t in DecoupledTrace.select()})
    if kind == "checkpoints":
        return ((name, None) for name in {c.name for c in DecoupledCheckpoint.select()})
    candidates = {"workloads": _workloads, "sysroots": _sysroots, "entries": _entries}.get(kind)
    return candidates(ctx, incomplete) if candidates else []


complete_sysroot = _completer(_sysroots)
complete_workload = _completer(_workloads)
complete_entry = _completer(_entries)
complete_other_entry = _completer(_other_entries)
complete_source_entry = _completer(_other_entries, _source_path)
complete_trace = _completer(_traces)
complete_checkpoint = _completer(_checkpoints)
complete_simpoint_checkpoint = _completer(_simpoint_checkpoints)
complete_by_kind = _completer(_by_kind)
complete_list_filter = _completer(_list_filter)
