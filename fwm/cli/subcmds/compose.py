"""
`fwm repo compose`: the commands that change the repository's content. Each needs write access to the repository; the
commands that only read it are elsewhere (`fwm repo bootstrap`, and the users' commands).
"""
import click

from fwm.cli.completion import (
    complete_by_kind, complete_checkpoint, complete_entry, complete_sysroot, complete_trace, complete_workload
)
from fwm.cli.fwm import open_repo, SectionedGroup
from fwm.data_loader.sim_version import read_sim_version, SimVersionError
from fwm.repo.errors import FwmValidationError

# The modules that read spike's files or catalog trees load pydantic: each command imports them when it runs, so that
# completing `fwm repo compose ...` stays light


@click.group(cls=SectionedGroup, short_help="Import, remove and change the repository's content.", sections=(
    ("Importing", ("add",)),
    ("Changing", ("remove", "set-metadata", "set-checkpoint-validation")),
))
def compose():
    """
    Change the repository's content: import sysroots, entry points, traces and checkpoints, remove them, set an entry
    point's metadata or a checkpoint's validation. Each command needs write access to the repository.
    """


@compose.group(short_help="Import a sysroot, entry, trace or checkpoint.")
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
    from fwm.repo.sysroot import add_sysroot
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
       "stdin_file": null, "boot_elf": "/boot/pk", "mem_mb": 2048, "nprocs": 1,
       "metadata": {"suite": "GAPBS", "language": ["C++"]}}
    In paths, "/" is the sysroot's root and "." the working directory. "metadata" is optional and free-form: any JSON
    object, kept and shown by fwm. With --exist-ok, an entry point that differs only in its metadata or description
    is the same one, and keeps its own; change its metadata with `fwm repo compose set-metadata`.
    """
    from fwm.repo.entry import add_entry
    click.echo(add_entry(open_repo(ctx, write=True), sysroot_name, workload_name, entry_point_name, entry_point_json,
                         description, exist_ok).name)


@add.command("trace")
@click.argument("entry", shell_complete=complete_entry)
@click.argument("recording_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--final-state", type=click.Path(exists=True, dir_okay=False),
              help="The +final-state-dump of the recording run: the instret of each HART.")
@click.option("--description", "-d", help="A description of the trace.")
@_exist_ok_option
@click.pass_context
def add_trace_cmd(ctx, entry, recording_dir, final_state, description, exist_ok):
    """
    Import RECORDING_DIR, a recording (+dev-traffic-record) of a run of the entry point ENTRY, as a trace, with its
    provenance (recording_provenance.json: when, on what machine and with which spike it was recorded), which it must
    have.

    Prints the UUID of the trace. A recording that already is a trace of the entry point fails with status 5, or,
    with --exist-ok and the same final state, prints that trace.
    """
    from fwm.repo.trace import add_trace
    click.echo(str(add_trace(open_repo(ctx, write=True), entry, recording_dir, final_state, description,
                             exist_ok).uuid))


@add.command("checkpoint")
@click.argument("trace", shell_complete=complete_trace)
@click.argument("desc", type=click.Path(exists=True, dir_okay=False))
@click.argument("checkpoint_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--simpoint-info", type=click.Path(exists=True, dir_okay=False),
              help='A JSON file of the SimPoint analysis: {"interval": 100000000, "maxK": 10, "checkpoints": '
                   '{"<name>": {"index": 3, "weight": 0.2531}, ...}}, naming exactly the checkpoints of DESC.')
@click.option("--description", "-d", help="A description of the checkpoints.")
@_sim_version_option
@_exist_ok_option
@click.pass_context
def add_checkpoint_cmd(ctx, trace, desc, checkpoint_dir, simpoint_info, description, sim_version_file, exist_ok):
    """
    Import the checkpoints that DESC, a description file, created from TRACE (a trace UUID or prefix, or an entry
    point with one trace) in CHECKPOINT_DIR, in one transaction.

    \b
    For a run directory R of `fwm repo bootstrap create-checkpoints`:
      fwm repo compose add checkpoint <trace> R/ckpt.desc R/checkpoints

    Each checkpoint DESC lists must be in CHECKPOINT_DIR, which catches one spike didn't create (it creates none at or
    after the program's exit); other files there are ignored. The requested instruction counts are kept.

    Prints each checkpoint as <trace UUID>:<name>, in the order of DESC.
    """
    from fwm.repo.checkpoint import add_checkpoints
    ckpts = add_checkpoints(open_repo(ctx, write=True), trace, desc, checkpoint_dir, simpoint_info=simpoint_info,
                            description=description, exist_ok=exist_ok, sim_version=_read_sim_version(sim_version_file))
    for ckpt in ckpts:
        click.echo(f"{ckpt.trace.uuid}:{ckpt.name}")


@compose.command("set-metadata", short_help="Replace an entry point's metadata.")
@click.argument("entry", shell_complete=complete_entry)
@click.argument("metadata_json", metavar="FILE", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def set_metadata_cmd(ctx, entry, metadata_json):
    """
    Replace the metadata of the entry point ENTRY with FILE, a JSON object, or null to remove it. Its traces are kept.

    Prints the name of the entry point.
    """
    from fwm.repo.entry import set_entry_metadata
    click.echo(set_entry_metadata(open_repo(ctx, write=True), entry, metadata_json).name)


@compose.command("remove", short_help="Remove a sysroot, entry, trace or checkpoint.")
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
    from fwm.repo import remove as rm
    r = open_repo(ctx, write=True)
    if kind == "sysroot":
        rm.remove_sysroot(r, id, recursive)
    elif kind == "entry":
        rm.remove_entry(r, id, recursive)
    elif kind == "trace":
        rm.remove_trace(r, id, recursive)
    else:
        rm.remove_checkpoint(r, id)


@compose.command("set-checkpoint-validation", short_help="Judge a checkpoint's run; record the result.")
@click.argument("checkpoint", shell_complete=complete_checkpoint)
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False))
@click.pass_context
def set_checkpoint_validation_cmd(ctx, checkpoint, run_dir):
    """
    Judge RUN_DIR, a run of CHECKPOINT to the end of the program (`fwm mkgen from-checkpoint`, run without -e), and
    set the checkpoint's validation to the result: "passed" or "failed", with why.

    It passes when every HART's final instruction count equals the trace's. Prints "passed" or "failed" and why;
    fails with status 4 if the checkpoint failed.
    """
    from fwm.repo.validation import record_validation, PASSED
    ckpt = record_validation(open_repo(ctx, write=True), checkpoint, run_dir)
    click.echo(f"{ckpt.validation_status}: {ckpt.validation_detail}")
    if ckpt.validation_status != PASSED:
        raise FwmValidationError(f"checkpoint {ckpt.trace.uuid}:{ckpt.name} failed its validation")
