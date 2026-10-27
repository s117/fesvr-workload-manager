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
    Import, remove and change the repository's content. Each command needs write access to the repository.

    Run them from one host at a time, e.g. a pipeline's controller: see the README, "Clusters and shared
    repositories".

    \b
    Examples:
      # Import a sysroot, and add an entry point that runs in it
      fwm repo compose add sysroot ./sysroots/road road.sg
      fwm repo compose add entry road.sg bfs bfs-road-n64 bfs-road-n64.json

    \b
      # Remove a trace, with its checkpoints
      fwm repo compose remove trace bfs-road-n64 -r
    """


@compose.group(cls=SectionedGroup, short_help="Import a sysroot, entry, trace or checkpoint.", sections=(
    ("In the order they depend on each other", ("sysroot", "entry", "trace", "checkpoint")),
))
def add():
    """
    Import a sysroot, an entry point, a trace or a checkpoint.

    Each prints what it imported. With --exist-ok, importing what the repository already has prints it and succeeds,
    so a pipeline can simply run it again. Without it, that fails with status 5.

    \b
    Examples:
      # Import a sysroot
      fwm repo compose add sysroot ./sysroots/road road.sg

    \b
      # Import a trace, in a pipeline that may run it again
      fwm repo compose add trace bfs-road-n64 run1/trace \\
        --final-state run1/final-state.json --exist-ok
    """


_sim_version_option = click.option(
    "--sim-version", "sim_version_file", type=click.Path(exists=True, dir_okay=False),
    help="A file with the `spike --version` output of the spike that made them.")


def _read_sim_version(path):
    if path is None:
        return None
    try:
        return read_sim_version(path)
    except (OSError, SimVersionError) as e:
        raise FwmValidationError(f"invalid --sim-version {path}: {e}")


_exist_ok_option = click.option(
    "--exist-ok", is_flag=True,
    help="If the same item is already there, print it and succeed (for reruns).")


@add.command("sysroot", short_help="Import a directory tree as a sysroot.")
@click.argument("directory", metavar="DIR", type=click.Path(exists=True, file_okay=False))
@click.argument("sysroot_name", metavar="NAME")
@click.option("--description", "-d", help="A description of the sysroot.")
@_exist_ok_option
@click.pass_context
def add_sysroot_cmd(ctx, directory, sysroot_name, description, exist_ok):
    """
    Import the directory tree DIR as the sysroot NAME.

    The tree is copied into the repository, with a catalog of its files, their permissions and checksums, and made
    read-only. Prints NAME.

    \b
    Examples:
      # Import a directory tree
      fwm repo compose add sysroot ./sysroots/road road.sg

    \b
      # Again, in a rerun: it succeeds if the same sysroot is there
      fwm repo compose add sysroot ./sysroots/road road.sg --exist-ok
    """
    from fwm.repo.sysroot import add_sysroot
    click.echo(add_sysroot(open_repo(ctx, write=True), directory, sysroot_name, description, exist_ok).name)


@add.command("entry", short_help="Add an entry point: how to launch a workload in a sysroot.")
@click.argument("sysroot_name", metavar="SYSROOT", shell_complete=complete_sysroot)
@click.argument("workload_name", metavar="WORKLOAD", shell_complete=complete_workload)
@click.argument("entry_point_name", metavar="NAME")
@click.argument("entry_point_json", metavar="JSON_FILE", type=click.Path(exists=True, dir_okay=False))
@click.option("--description", "-d", help="A description of the entry point.")
@_exist_ok_option
@click.pass_context
def add_entry_cmd(ctx, sysroot_name, workload_name, entry_point_name, entry_point_json, description, exist_ok):
    """
    Add the entry point NAME: how to run the workload WORKLOAD in the sysroot SYSROOT. Prints NAME.

    JSON_FILE says how to launch it, e.g.:

    \b
      {"command": "bfs -f road.sg -n64", "working_directory": "/app",
       "envs": null, "stdin_file": null, "boot_elf": "/boot/pk",
       "mem_mb": 2048, "nprocs": 1, "copy_spawn": true,
       "metadata": {"suite": "GAPBS", "language": ["C++"]}}

    \b
    - In paths, "/" is the sysroot's root, and "." the working directory.
    - copy_spawn (optional, default true): how `fwm spawn` gives a run the
      sysroot. false links to the repository's files: fast, but read-only.
    - metadata (optional): any JSON object, kept and shown by fwm. Change it
      with `fwm repo compose set-metadata`; --exist-ok doesn't compare it.

    \b
    Examples:
      # Run workload bfs in sysroot road.sg, as bfs-road-n64.json says
      fwm repo compose add entry road.sg bfs bfs-road-n64 bfs-road-n64.json
    """
    from fwm.repo.entry import add_entry
    click.echo(add_entry(open_repo(ctx, write=True), sysroot_name, workload_name, entry_point_name, entry_point_json,
                         description, exist_ok).name)


@add.command("trace", short_help="Import a recording of an entry point's run as a trace.")
@click.argument("entry", shell_complete=complete_entry)
@click.argument("recording_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--final-state", type=click.Path(exists=True, dir_okay=False),
              help="The run's final state (final-state.json): each processor's instruction count. Without it, the "
                   "trace's checkpoints can't be validated.")
@click.option("--description", "-d", help="A description of the trace.")
@_exist_ok_option
@click.pass_context
def add_trace_cmd(ctx, entry, recording_dir, final_state, description, exist_ok):
    """
    Import a recording of a run of the entry point ENTRY as a trace. Prints its UUID.

    RECORDING_DIR is the trace/ directory of a record run (see `fwm repo bootstrap mkgen`). It holds how the
    recording was made (when, where, with which spike), which the trace keeps. A recording that already is a trace of
    ENTRY fails with status 5, or with --exist-ok prints that trace.

    \b
    Examples:
      # Import the trace of a record run
      fwm repo compose add trace bfs-road-n64 run1/trace \\
        --final-state run1/final-state.json
    """
    from fwm.repo.trace import add_trace
    click.echo(str(add_trace(open_repo(ctx, write=True), entry, recording_dir, final_state, description,
                             exist_ok).uuid))


@add.command("checkpoint", short_help="Import the checkpoints that a run created from a trace.")
@click.argument("trace", shell_complete=complete_trace)
@click.argument("desc", type=click.Path(exists=True, dir_okay=False))
@click.argument("checkpoint_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--simpoint-info", type=click.Path(exists=True, dir_okay=False),
              help="The SimPoint analysis of the checkpoints, as JSON (see the README, Quick start).")
@click.option("--description", "-d", help="A description of the checkpoints.")
@_sim_version_option
@_exist_ok_option
@click.pass_context
def add_checkpoint_cmd(ctx, trace, desc, checkpoint_dir, simpoint_info, description, sim_version_file, exist_ok):
    """
    Import the checkpoints that a run created from TRACE, in one transaction.

    DESC is the run's description of the checkpoints, and CHECKPOINT_DIR the directory they were created in. Every
    checkpoint DESC lists must be there: spike creates none at or after the program's exit. Prints each checkpoint as
    TRACE_UUID:NAME.

    \b
    Examples:
      # Import the checkpoints of a create-checkpoints run
      fwm repo compose add checkpoint bfs-road-n64 \\
        run2/ckpt.desc run2/checkpoints \\
        --simpoint-info simpoints.json --sim-version sim-version.txt
    """
    from fwm.repo.checkpoint import add_checkpoints
    ckpts = add_checkpoints(open_repo(ctx, write=True), trace, desc, checkpoint_dir, simpoint_info=simpoint_info,
                            description=description, exist_ok=exist_ok, sim_version=_read_sim_version(sim_version_file))
    for ckpt in ckpts:
        click.echo(f"{ckpt.trace.uuid}:{ckpt.name}")


@compose.command("set-metadata", short_help="Replace an entry point's metadata.")
@click.argument("entry", shell_complete=complete_entry)
@click.argument("metadata_json", metavar="JSON_FILE", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def set_metadata_cmd(ctx, entry, metadata_json):
    """
    Replace the metadata of the entry point ENTRY, keeping its traces. Prints ENTRY.

    JSON_FILE holds a JSON object, or null to remove the metadata.

    \b
    Examples:
      # Replace the metadata of an entry point
      fwm repo compose set-metadata bfs-road-n64 metadata.json
    """
    from fwm.repo.entry import set_entry_metadata
    click.echo(set_entry_metadata(open_repo(ctx, write=True), entry, metadata_json).name)


@compose.command("remove", short_help="Remove a sysroot, entry, trace or checkpoint.")
@click.argument("kind", type=click.Choice(["sysroot", "entry", "trace", "checkpoint"]), metavar="KIND")
@click.argument("id", metavar="NAME", shell_complete=complete_by_kind)
@click.option("--recursive", "-r", is_flag=True, help="Also remove what depends on it.")
@click.pass_context
def remove_cmd(ctx, kind, id, recursive):
    """
    Remove an item, and with --recursive, what depends on it.

    KIND is sysroot, entry, trace or checkpoint, and NAME the item's name. A sysroot with entry points, an entry point
    with traces, or a trace with checkpoints is removed only with --recursive.

    \b
    Examples:
      # One checkpoint
      fwm repo compose remove checkpoint bfs-road-n64.3.0.25

    \b
      # An entry point, with its traces and their checkpoints
      fwm repo compose remove entry bfs-road-n64 -r
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
    Judge a run of CHECKPOINT to its end, and record "passed" or "failed" with the checkpoint.

    RUN_DIR is a `fwm mkgen from-checkpoint` run of CHECKPOINT, run without -e. It passes when the program exited as
    in the trace: with the same exit status, and the same instruction count on every processor. It prints "passed" or
    "failed" and why, and fails with status 4 when the checkpoint failed.

    \b
    Examples:
      # Run a checkpoint to the end, then judge the run
      fwm mkgen from-checkpoint bfs-road-n64.3.0.25 -o run3
      make -C run3 run
      fwm repo compose set-checkpoint-validation bfs-road-n64.3.0.25 run3
    """
    from fwm.repo.validation import record_validation, PASSED
    ckpt = record_validation(open_repo(ctx, write=True), checkpoint, run_dir)
    click.echo(f"{ckpt.validation_status}: {ckpt.validation_detail}")
    if ckpt.validation_status != PASSED:
        raise FwmValidationError(f"checkpoint {ckpt.trace.uuid}:{ckpt.name} failed its validation")
