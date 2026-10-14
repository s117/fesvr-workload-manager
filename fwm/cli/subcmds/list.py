import click

from fwm.cli.completion import complete_list_filter, complete_sysroot, complete_trace, complete_workload
from fwm.cli.fwm import open_repo
from fwm.cli.output import echo_json, echo_table
from fwm.repo import query
from fwm.repo.trace import resolve_trace


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

    if kind == "workloads":
        rows = query.list_workloads(name_filter)
        columns = [("name", "WORKLOAD"), ("entries", "ENTRIES")]
    elif kind == "sysroots":
        rows = query.list_sysroots(r, name_filter)
        columns = [("name", "SYSROOT"), ("num_files", "FILES"), ("size_bytes", "BYTES"), ("entries", "ENTRIES")]
    elif kind == "entries":
        rows = query.list_entries(name_filter, workload, sysroot)
        for row in rows:
            row["num_traces"] = len(row["traces"])
        columns = [("name", "ENTRY"), ("workload", "WORKLOAD"), ("sysroot", "SYSROOT"), ("mem_mb", "MEM_MB"),
                   ("num_traces", "TRACES"), ("command", "COMMAND")]
    elif kind == "traces":
        rows = query.list_traces(r, name_filter)
        for row in rows:
            row["instret"] = [h["instret"] for h in row["harts"]]
            row["num_checkpoints"] = len(row["checkpoints"])
        columns = [("uuid", "TRACE"), ("entry", "ENTRY"), ("complete", "COMPLETE"), ("exit_status", "EXIT"),
                   ("instret", "INSTRET"), ("num_checkpoints", "CHECKPOINTS")]
    else:
        rows = query.list_checkpoints(r, name_filter, resolve_trace(trace_ref) if trace_ref else None)
        for row in rows:
            row["csr_count"] = [h["csr_count"] for h in row["harts"]]
            row["validated"] = row["validation"]["status"] if row["validation"] else None
        columns = [("name", "CHECKPOINT"), ("entry", "ENTRY"), ("trace", "TRACE"), ("simpoint_weight", "WEIGHT"),
                   ("simpoint_interval", "INTERVAL"), ("csr_count", "COUNT"), ("validated", "VALIDATION")]

    if as_json:
        echo_json(rows)
    else:
        echo_table(rows, columns)
