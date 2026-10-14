import os

import click

from fwm.cli.completion import complete_by_kind, complete_entry, complete_sysroot, complete_trace, complete_workload
from fwm.cli.fwm import open_repo
from fwm.data_loader.sim_version import read_sim_version, SimVersionError
from fwm.database.schema import Sysroot, DecoupledTrace, DecoupledCheckpoint
from fwm.repo import remove as rm
from fwm.repo.checkpoint import add_checkpoint, resolve_checkpoint, verify_checkpoint
from fwm.repo.entry import add_entry
from fwm.repo.errors import FwmValidationError
from fwm.repo.gc import collect_garbage, find_leftovers
from fwm.repo.repo import Repo
from fwm.repo.sysroot import add_sysroot, get_sysroot, verify_sysroot
from fwm.repo.trace import add_trace, resolve_trace, verify_trace
from fwm.utils.log import Logger


@click.group()
def repo():
    """
    Create and change the workload repository.
    """


@repo.command()
@click.argument("path", type=click.Path(file_okay=False))
def init(path):
    """
    Create an empty workload repository at PATH.
    """
    Repo.init(path).close()
    click.echo(os.path.abspath(path))


@repo.group()
def add():
    """
    Add a sysroot, an entry point, a trace or a checkpoint.
    """


_sim_version_option = click.option(
    "--sim-version", "sim_version_file", type=click.Path(exists=True, dir_okay=False),
    help="A file with the `spike --version` output of the simulator that made it, kept for provenance.")


def _read_sim_version(path):
    if path is None:
        return None
    try:
        return read_sim_version(path)
    except (OSError, SimVersionError) as e:
        raise FwmValidationError(f"invalid --sim-version {path}: {e}")


_exist_ok_option = click.option(
    "--exist-ok", is_flag=True,
    help="If the same item is already in the repository, print it and succeed instead of failing with status 5.")


@add.command("sysroot")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.argument("sysroot_name")
@click.option("--description", "-d", help="A description of the sysroot.")
@_exist_ok_option
@click.pass_context
def add_sysroot_cmd(ctx, directory, sysroot_name, description, exist_ok):
    """
    Import DIRECTORY as the sysroot SYSROOT_NAME.

    The tree is copied into the repository, cataloged with its permissions, and made read-only.
    """
    click.echo(add_sysroot(open_repo(ctx, write=True), directory, sysroot_name, description, exist_ok).name)


@add.command("entry")
@click.argument("sysroot_name", shell_complete=complete_sysroot)
@click.argument("workload_name", shell_complete=complete_workload)
@click.argument("entry_point_name")
@click.argument("entry_point_json", type=click.Path(exists=True, dir_okay=False))
@click.option("--description", "-d", help="A description of the entry point.")
@_exist_ok_option
@click.pass_context
def add_entry_cmd(ctx, sysroot_name, workload_name, entry_point_name, entry_point_json, description, exist_ok):
    """
    Add the entry point ENTRY_POINT_NAME: how to launch the workload WORKLOAD_NAME in the sysroot SYSROOT_NAME.

    \b
    ENTRY_POINT_JSON holds the launch fields, e.g.
      {"command": "bfs -f road.sg -n64", "working_directory": "/app", "envs": null,
       "stdin_file": null, "boot_elf": "/boot/pk", "mem_mb": 2048, "nprocs": 1}
    In paths, "/" is the sysroot's root and "." the working directory.
    """
    click.echo(add_entry(open_repo(ctx, write=True), sysroot_name, workload_name, entry_point_name, entry_point_json,
                         description, exist_ok).name)


@add.command("trace")
@click.argument("entry", shell_complete=complete_entry)
@click.argument("recording_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--final-state", type=click.Path(exists=True, dir_okay=False),
              help="The +final-state-dump of the recording run: the instret of each HART.")
@click.option("--description", "-d", help="A description of the trace.")
@_sim_version_option
@_exist_ok_option
@click.pass_context
def add_trace_cmd(ctx, entry, recording_dir, final_state, description, sim_version_file, exist_ok):
    """
    Import RECORDING_DIR, a recording (+dev-traffic-record) of a run of the entry point ENTRY, as a trace.

    Prints the UUID of the trace. A recording that already is a trace of the entry point fails with status 5, or,
    with --exist-ok and the same final state, prints that trace.
    """
    click.echo(str(add_trace(open_repo(ctx, write=True), entry, recording_dir, final_state, description, exist_ok,
                             _read_sim_version(sim_version_file)).uuid))


@add.command("checkpoint")
@click.argument("trace", shell_complete=complete_trace)
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--name", help="The checkpoint's name. Defaults to the file name.")
@click.option("--simpoint-weight", type=float,
              help="Its SimPoint weight. Defaults to the one in its name, when the name is "
                   "<entry>.<simpoint index>.<weight>.")
@click.option("--description", "-d", help="A description of the checkpoint.")
@_sim_version_option
@_exist_ok_option
@click.pass_context
def add_checkpoint_cmd(ctx, trace, file, name, simpoint_weight, description, sim_version_file, exist_ok):
    """
    Import FILE, a checkpoint created from TRACE (a trace UUID or prefix, or an entry point with one trace).

    Prints the checkpoint as <trace UUID>:<name>.
    """
    ckpt = add_checkpoint(open_repo(ctx, write=True), trace, file, name, simpoint_weight, description, exist_ok,
                          _read_sim_version(sim_version_file))
    click.echo(f"{ckpt.trace.uuid}:{ckpt.name}")


@repo.command("remove")
@click.argument("kind", type=click.Choice(["sysroot", "entry", "trace", "checkpoint"]))
@click.argument("id", shell_complete=complete_by_kind)
@click.option("--recursive", "-r", is_flag=True, help="Also remove what depends on it.")
@click.pass_context
def remove_cmd(ctx, kind, id, recursive):
    """
    Remove a sysroot, an entry point, a trace or a checkpoint.

    A sysroot with entry points, an entry point with traces, or a trace with checkpoints is removed only with
    --recursive.
    """
    r = open_repo(ctx, write=True)
    if kind == "sysroot":
        rm.remove_sysroot(r, id, recursive)
    elif kind == "entry":
        rm.remove_entry(r, id, recursive)
    elif kind == "trace":
        rm.remove_trace(r, id, recursive)
    else:
        rm.remove_checkpoint(r, id)


@repo.command("verify")
@click.argument("kind", type=click.Choice(["sysroot", "trace", "checkpoint"]), required=False)
@click.argument("id", required=False, shell_complete=complete_by_kind)
@click.pass_context
def verify_cmd(ctx, kind, id):
    """
    Verify the content of the repository against its catalogs: everything, or one sysroot, trace or checkpoint.

    Verifying everything also reports the leftovers of interrupted commands, which `fwm repo gc` removes. Prints each
    problem found, and fails if there is any.
    """
    r = open_repo(ctx)
    if kind is not None and id is None:
        raise click.UsageError(f"give the {kind} to verify")

    items = []
    if kind in (None, "sysroot"):
        sysroots = [get_sysroot(id)] if kind else list(Sysroot.select().order_by(Sysroot.name))
        items += [(f"sysroot {s.name}", lambda s=s: verify_sysroot(r, s)) for s in sysroots]
    if kind in (None, "trace"):
        traces = [resolve_trace(id)] if kind else list(DecoupledTrace.select())
        items += [(f"trace {t.uuid}", lambda t=t: verify_trace(r, t)) for t in traces]
    if kind in (None, "checkpoint"):
        ckpts = [resolve_checkpoint(id)] if kind else list(DecoupledCheckpoint.select())
        items += [(f"checkpoint {c.trace.uuid}:{c.name}", lambda c=c: verify_checkpoint(r, c)) for c in ckpts]

    failed = 0
    for label, verify in items:
        problems = verify()
        if problems:
            failed += 1
        for path, reason in problems:
            click.echo(f"{label}: {path}: {reason}")
    leftovers = find_leftovers(r) if kind is None else []
    for leftover in leftovers:
        click.echo(f"leftover {leftover.path}: {leftover.reason}")
    Logger.get_logger().info(
        f"verified {len(items)} item(s), {failed} with problems" +
        (f"; {len(leftovers)} leftover(s), which `fwm repo gc` removes" if leftovers else ""))
    if failed or leftovers:
        raise FwmValidationError(
            f"{failed} item(s) failed the verification" + (f", {len(leftovers)} leftover(s)" if leftovers else ""))


@repo.command("gc")
@click.option("--dry-run", "-n", is_flag=True, help="Only print what would be removed.")
@click.pass_context
def gc_cmd(ctx, dry_run):
    """
    Remove the leftovers of interrupted commands: the staging of imports that were killed, and files that no
    sysroot, trace or checkpoint refers to.

    Prints each leftover removed (or, with --dry-run, found).
    """
    r = open_repo(ctx, write=not dry_run)
    leftovers = find_leftovers(r) if dry_run else collect_garbage(r)
    for leftover in leftovers:
        click.echo(f"{leftover.path}: {leftover.reason}")
    Logger.get_logger().info(f"{'found' if dry_run else 'removed'} {len(leftovers)} leftover(s)")
