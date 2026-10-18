import click

from fwm.cli.completion import complete_by_kind
from fwm.cli.fwm import open_repo
from fwm.cli.output import echo_json, echo_view, summarize_view
from fwm.database.schema import Workload
from fwm.repo import query
from fwm.repo.checkpoint import resolve_checkpoint
from fwm.repo.entry import get_entry
from fwm.repo.errors import NotFoundError
from fwm.repo.sysroot import get_sysroot
from fwm.repo.trace import resolve_trace


@click.command()
@click.argument("kind", type=click.Choice(["workload", "sysroot", "entry", "trace", "checkpoint"]))
@click.argument("id", shell_complete=complete_by_kind)
@click.option("--json", "as_json", is_flag=True, help="Print JSON, for scripts.")
@click.pass_context
def show(ctx, kind, id, as_json):
    """
    Show the details of a workload, sysroot, entry point, trace or checkpoint.

    \b
    A trace is given by its UUID (a unique prefix is enough), or by an entry point that has one trace.
    A checkpoint is given by <trace>:<name>, or by its name when it is unique.
    """
    r = open_repo(ctx)
    if kind == "workload":
        workload = Workload.get_or_none(Workload.name == id)
        if workload is None:
            raise NotFoundError(f"no workload named '{id}'")
        view = query.workload_view(workload)
    elif kind == "sysroot":
        view = query.sysroot_view(r, get_sysroot(id))
    elif kind == "entry":
        view = query.entry_view(get_entry(id))
    elif kind == "trace":
        view = query.trace_view(r, resolve_trace(id))
    else:
        view = query.checkpoint_view(r, resolve_checkpoint(id))

    if as_json:
        echo_json(view)
    else:
        echo_view(summarize_view(view))
