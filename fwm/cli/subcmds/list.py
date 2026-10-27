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

    KIND is workloads, sysroots, entries, traces or checkpoints. Traces are listed by UUID, and checkpoints as
    `fwm show checkpoint` takes them. `fwm show` gives the details of each.

    A filter keeps what is related to a matching item, directly or through others. Give a filter more than once to
    allow any of its patterns. With several filters, only what all of them keep is listed. A GLOB is a name, or a shell
    pattern: quote it.

    \b
    Examples:
      fwm list                               all entry points
      fwm list checkpoints --workload bfs    checkpoints of bfs's entry points
      fwm list entries --entry 'bfs-*'       names starting with bfs-
      fwm list sysroots --checkpoint c1      the sysroot c1 was made in
      fwm list traces --json                 for scripts
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
