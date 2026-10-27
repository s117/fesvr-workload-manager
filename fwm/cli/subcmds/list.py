import click

from fwm.cli.completion import (
    complete_checkpoint_name, complete_entry, complete_sysroot, complete_trace_uuid, complete_workload
)
from fwm.cli.fwm import open_repo
from fwm.cli.output import echo_json
from fwm.repo.listing import KINDS, PLURALS, list_names
from fwm.utils.log import Logger

_FILTER_HELP = {
    "workload": "Only those related to a workload whose name matches GLOB.",
    "sysroot": "Only those related to a sysroot whose name matches GLOB.",
    "entry": "Only those related to an entry point whose name matches GLOB.",
    "trace": "Only those related to a trace whose UUID matches GLOB.",
    "checkpoint": "Only those related to a checkpoint whose name matches GLOB, or <trace UUID GLOB>:<name GLOB>.",
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
@click.argument("kind", type=click.Choice([PLURALS[k] for k in KINDS]), default="entries", required=False)
@_filter_options
@click.option("--json", "as_json", is_flag=True, help="Print a JSON array of the names, for scripts.")
@click.pass_context
def list_cmd(ctx, kind, workload_globs, sysroot_globs, entry_globs, trace_globs, checkpoint_globs, as_json):
    """
    List the names of the workloads, sysroots, entry points (the default), traces or checkpoints of the repository:
    traces by UUID, and checkpoints as `fwm show checkpoint` takes them. `fwm show` gives the details of each.

    A filter keeps those related to an entity it matches: directly, or through others. A workload or sysroot is
    related to its entry points, an entry point to its workload, sysroot and traces, and a trace to its checkpoints.
    E.g. `fwm list checkpoints --workload bfs` lists the checkpoints of the traces of bfs's entry points, and
    `fwm list entries --entry 'bfs-*'` the entry points whose name starts with "bfs-".

    A GLOB is a shell-style pattern (*, ?, [...]; quote it in the shell), or an exact name. A filter given more than
    once keeps what matches any of its GLOBs. With several filters, the result is what each of them keeps.
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
