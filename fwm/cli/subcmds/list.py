from typing import List, Tuple

import click

from fwm.cli.completion import complete_list_filter, complete_sysroot, complete_trace, complete_workload
from fwm.cli.fwm import open_repo
from fwm.cli.output import echo_json, echo_table, Column
from fwm.repo import query
from fwm.repo.lookup import resolve_trace


@click.command("list")
@click.argument("kind", type=click.Choice(["workloads", "sysroots", "entries", "traces", "checkpoints"]),
                default="entries", required=False)
@click.argument("name_filter", metavar="FILTER", required=False, shell_complete=complete_list_filter)
@click.option("--workload", shell_complete=complete_workload, help="Only the entry points of this workload.")
@click.option("--sysroot", shell_complete=complete_sysroot, help="Only the entry points in this sysroot.")
@click.option("--trace", "trace_ref", shell_complete=complete_trace, help="Only the checkpoints of this trace.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON, for scripts.")
@click.pass_context
def list_cmd(ctx, kind, name_filter, workload, sysroot, trace_ref, as_json):
    """
    List the workloads, sysroots, entry points (the default), traces or checkpoints of the repository, whose name
    contains FILTER (for traces, the name of their entry point).
    """
    r = open_repo(ctx)
    if (workload or sysroot) and kind != "entries":
        raise click.UsageError("--workload and --sysroot apply to entries")
    if trace_ref and kind != "checkpoints":
        raise click.UsageError("--trace applies to checkpoints")

    # Each column is (heading, the value it shows): a key of the view, or a function of the view. The views themselves
    # are printed by --json, the same as `fwm show --json` prints them.
    columns: List[Tuple[str, Column]]
    if kind == "workloads":
        rows = query.list_workloads(name_filter)
        columns = [("WORKLOAD", "name"), ("ENTRIES", "entries")]
    elif kind == "sysroots":
        rows = query.list_sysroots(r, name_filter)
        columns = [("SYSROOT", "name"), ("FILES", "num_files"), ("BYTES", "size_bytes"), ("ENTRIES", "entries")]
    elif kind == "entries":
        rows = query.list_entries(name_filter, workload, sysroot)
        columns = [("ENTRY", "name"), ("WORKLOAD", "workload"), ("SYSROOT", "sysroot"), ("MEM_MB", "mem_mb"),
                   ("TRACES", lambda v: len(v["traces"])), ("CHECKPOINTS", "num_checkpoints"), ("COMMAND", "command")]
    elif kind == "traces":
        rows = query.list_traces(r, name_filter)
        columns = [("TRACE", "uuid"), ("ENTRY", "entry"), ("RECORDED ON", lambda v: v["provenance"]["uname"]["nodename"]),
                   ("COMPLETE", "complete"), ("EXIT", "exit_status"),
                   ("INSTRET", lambda v: [h["instret"] for h in v["harts"]]),
                   ("CHECKPOINTS", lambda v: len(v["checkpoints"]))]
    else:
        rows = query.list_checkpoints(r, name_filter, resolve_trace(trace_ref) if trace_ref else None)
        columns = [("CHECKPOINT", "name"), ("ENTRY", "entry"), ("TRACE", "trace"), ("WEIGHT", "simpoint_weight"),
                   ("INTERVAL", "simpoint_interval"), ("COUNT", lambda v: [h["csr_count"] for h in v["harts"]]),
                   ("VALIDATION", lambda v: v["validation"]["status"] if v["validation"] else None)]

    if as_json:
        echo_json(rows)
    else:
        echo_table(rows, columns)
