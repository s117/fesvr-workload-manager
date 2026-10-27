import os
from typing import Optional

import click

from fwm.cli.completion import complete_checkpoint, complete_entry, complete_simpoint_checkpoint, complete_trace
from fwm.cli.fwm import open_repo, SectionedGroup
from fwm.mkgen.makefile import (
    generate_from_sysroot, generate_from_fesvr_trace, generate_from_checkpoint, generate_from_simpoint
)
from fwm.repo.lookup import resolve_checkpoint
from fwm.utils.log import Logger

out_dir_option = click.option("--out-dir", "-o", type=click.Path(file_okay=False), default=".", show_default=True,
                              help="The run directory: new, empty, or generated before for the same kind of run.")


def done(makefile: str, hint: Optional[str] = None) -> None:
    """Print the path of a generated Makefile, and how to run it."""
    click.echo(makefile)
    Logger.get_logger().info(f"run it with: make -C {os.path.dirname(makefile)} run")
    if hint:
        Logger.get_logger().info(hint)


@click.group(cls=SectionedGroup, sections=(
    ("Where to begin", ("from-sysroot", "from-fesvr-trace", "from-checkpoint", "from-simpoint")),
))
def mkgen():
    """
    Generate a run directory that starts a simulation: a Makefile, and run.json, which says what the run uses.

    Run it with `make -C DIR run`. Every run writes final-state.json: why it ended (e.g. target_exit, or instret_limit
    for -e), and the instruction count of each processor (HART).

    \b
    Variables to set on make's command line:
      SIM                the simulator (default: FWM_SIM_CMD, else spike)
      SIM_FLAGS_EXTRA    spike options, e.g. -e or -s (-m and -p are set)
      FESVR_FLAGS_EXTRA  FESVR options, e.g. +verbose

    \b
    Examples:
      # Simulate 100M instructions from a checkpoint, with another simulator
      fwm mkgen from-checkpoint bfs-road-n64.3.0.25 -o run1
      make -C run1 run SIM=/path/to/721sim SIM_FLAGS_EXTRA=-e100000000
    """


@mkgen.command("from-sysroot", short_help="From the start, in a copy of its sysroot (Linux only).")
@click.argument("entry", shell_complete=complete_entry)
@out_dir_option
@click.pass_context
def from_sysroot(ctx, entry, out_dir):
    """
    Run an entry point's program from its start, in a fresh copy of its sysroot. Linux only.

    The program does its real system calls. It prints its console, also saved in console.stdout and console.stderr,
    and writes its files in the run's sysroot/. The host answers those calls, so the instruction count can differ
    from the trace's (e.g. with where the console goes): count instructions with a replay.

    \b
    Examples:
      # Run the real program, which prints its console
      fwm mkgen from-sysroot bfs-road-n64 -o run1
      make -C run1 run
    """
    done(generate_from_sysroot(open_repo(ctx), entry, out_dir))


@mkgen.command("from-fesvr-trace", short_help="From the start, replaying its trace (any host).")
@click.argument("trace", shell_complete=complete_trace)
@out_dir_option
@click.pass_context
def from_fesvr_trace(ctx, trace, out_dir):
    """
    Replay a trace from the start of its program: no sysroot needed, on any host.

    TRACE is a trace's UUID (a unique prefix is enough), or an entry point that has one trace. A replay checks that the
    program computes the same data. The program prints nothing and writes no file.

    \b
    Examples:
      # Replay the trace of an entry point
      fwm mkgen from-fesvr-trace bfs-road-n64 -o run1
      make -C run1 run
    """
    done(generate_from_fesvr_trace(open_repo(ctx), trace, out_dir))


@mkgen.command("from-checkpoint", short_help="From a checkpoint to the end of the program.")
@click.argument("checkpoint", shell_complete=complete_checkpoint)
@out_dir_option
@click.pass_context
def from_checkpoint(ctx, checkpoint, out_dir):
    """
    Simulate from a checkpoint to the end of the program.

    CHECKPOINT is TRACE:NAME, or NAME when no other trace has a checkpoint of that name. To stop after n instructions,
    add SIM_FLAGS_EXTRA=-e<n> to make. A run to the end can validate the checkpoint, as the last example does.

    \b
    Examples:
      # The first 100M instructions after a checkpoint
      fwm mkgen from-checkpoint bfs-road-n64.3.0.25 -o run1
      make -C run1 run SIM_FLAGS_EXTRA=-e100000000

    \b
      # To the end of the program, then record whether it validates the checkpoint
      fwm mkgen from-checkpoint bfs-road-n64.3.0.25 -o run2
      make -C run2 run
      fwm repo compose set-checkpoint-validation bfs-road-n64.3.0.25 run2
    """
    r = open_repo(ctx)
    makefile = generate_from_checkpoint(r, checkpoint, out_dir)
    hint = None
    if resolve_checkpoint(checkpoint).simpoint_interval is not None:
        hint = f"to simulate its SimPoint interval only, use: fwm mkgen from-simpoint {checkpoint}"
    done(makefile, hint)


@mkgen.command("from-simpoint", short_help="One SimPoint interval, from its checkpoint.")
@click.argument("checkpoint", shell_complete=complete_simpoint_checkpoint)
@out_dir_option
@click.pass_context
def from_simpoint(ctx, checkpoint, out_dir):
    """
    Simulate the SimPoint interval of a checkpoint, for sampled simulation.

    The checkpoint must have SimPoint information (see `fwm show checkpoint`). The run stops at the interval's end;
    INTERVAL=<n> on make's command line changes its length. Weigh its results by the checkpoint's simpoint_weight.

    spike exits 1 when it stops a run that way, so make reports "Error 1": this run ends that way normally.

    \b
    Examples:
      # Simulate the interval with another simulator
      fwm mkgen from-simpoint bfs-road-n64.3.0.25 -o run1
      make -C run1 run SIM=/path/to/721sim
    """
    done(generate_from_simpoint(open_repo(ctx), checkpoint, out_dir))
