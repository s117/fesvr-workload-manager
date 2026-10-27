import click

from fwm.cli.completion import (
    complete_checkpoint_name, complete_entry, complete_sysroot, complete_trace_uuid, complete_workload
)
from fwm.cli.fwm import open_repo
from fwm.cli.output import echo_json
from fwm.repo.listing import KINDS, PLURALS, list_names
from fwm.utils.log import Logger

_FILTER_HELP = {
    "workload": "Related to a matching workload.",
    "sysroot": "Related to a matching sysroot.",
    "entry": "Related to a matching entry point.",
    "trace": "Related to a trace whose UUID matches.",
    "checkpoint": "Related to a matching checkpoint: NAME, or TRACE_UUID:NAME.",
}
_COMPLETERS = {"workload": complete_workload, "sysroot": complete_sysroot, "entry": complete_entry,
               "trace": complete_trace_uuid, "checkpoint": complete_checkpoint_name}
_SINGULAR = {plural: kind for kind, plural in PLURALS.items()}


def _filter_options(f):
    for kind in reversed(KINDS):
        f = click.option(f"--{kind}", f"{kind}_globs", metavar="GLOB", multiple=True,
                         shell_complete=_COMPLETERS[kind], help=_FILTER_HELP[kind])(f)
    return f


@click.command("list")
@click.argument("kind", type=click.Choice([PLURALS[k] for k in KINDS]), default="entries", required=False,
                metavar="[KIND]")
@_filter_options
@click.option("--json", "as_json", is_flag=True, help="Print JSON, for scripts.")
@click.pass_context
def list_cmd(ctx, kind, workload_globs, sysroot_globs, entry_globs, trace_globs, checkpoint_globs, as_json):
    """
    List the names of the workloads, sysroots, entries (the default), traces or checkpoints of the repository.

    With filters, it is the way to search the repository. Ask for one kind of item, and filter by the others: e.g.
    the checkpoints of a workload, the entry points in a sysroot, or the sysroot a checkpoint was made in. Then
    `fwm show KIND NAME` gives the details of each item found.

    \b
    How the filters work:
    - A filter keeps what is related to a matching item, directly or through
      others: e.g. a workload's checkpoints are those of its entry points'
      traces.
    - A GLOB is a name, or a shell pattern such as 'bfs-*' (quote it).
    - Give a filter more than once to allow any of its patterns.
    - With several filters, only what all of them keep is listed.

    KIND is workloads, sysroots, entries, traces or checkpoints. Traces are listed by UUID, and checkpoints as
    `fwm show checkpoint` takes them.

    \b
    Examples:
      # All entry points
      fwm list

    \b
      # The checkpoints of workload bfs's entry points
      fwm list checkpoints --workload bfs

    \b
      # The entry points whose name starts with bfs-
      fwm list entries --entry 'bfs-*'

    \b
      # The bfs entry points in sysroot road.sg, as JSON for scripts
      fwm list entries --workload bfs --sysroot road.sg --json

    \b
      # The sysroot a checkpoint was made in
      fwm list sysroots --checkpoint bfs-road-n64.3.0.25
    """
    open_repo(ctx)
    globs = {"workload": workload_globs, "sysroot": sysroot_globs, "entry": entry_globs, "trace": trace_globs,
             "checkpoint": checkpoint_globs}
    listing = list_names(_SINGULAR[kind], globs)
    for filter_kind, glob in listing.unmatched:
        Logger.get_logger().warning(f"no {filter_kind} matches {glob!r}")
    if as_json:
        echo_json(listing.names)
    else:
        for name in listing.names:
            click.echo(name)
