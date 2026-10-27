import click

from fwm.cli.completion import complete_by_kind
from fwm.cli.fwm import open_repo
from fwm.cli.output import echo_json, echo_view, summarize_view
from fwm.database.schema import Workload
from fwm.repo import query
from fwm.repo.lookup import get_entry, get_sysroot, resolve_checkpoint, resolve_trace
from fwm.repo.errors import NotFoundError


@click.command()
@click.argument("kind", type=click.Choice(["workload", "sysroot", "entry", "trace", "checkpoint"]), metavar="KIND")
@click.argument("id", metavar="NAME", shell_complete=complete_by_kind)
@click.option("--json", "as_json", is_flag=True, help="Print JSON, for scripts.")
@click.pass_context
def show(ctx, kind, id, as_json):
    """
    Show the details of one workload, sysroot, entry, trace or checkpoint.

    KIND is workload, sysroot, entry, trace or checkpoint, and NAME the item's name. A trace is named by its UUID (a
    unique prefix is enough), or by an entry point that has one trace. A checkpoint is named TRACE:NAME, or NAME alone
    when no other trace has a checkpoint of that name.

    \b
    Examples:
      fwm show entry bfs-road-n64
      fwm show trace bfs-road-n64                 the trace of an entry point
      fwm show checkpoint bfs-road-n64.3.0.25 --json
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
