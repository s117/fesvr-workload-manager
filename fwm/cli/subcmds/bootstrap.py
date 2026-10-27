"""
`fwm repo bootstrap`: the steps that make the repository's traces and checkpoints, for its creator (in practice the
fwm-bootstrap pipeline). None of them writes to the repository: they generate runs and check their outputs, which
`fwm repo compose` then imports.
"""
import click

from fwm.cli.completion import complete_entry, complete_trace
from fwm.cli.fwm import open_repo, SectionedGroup
from fwm.cli.output import echo_json
from fwm.cli.subcmds.mkgen import done, out_dir_option
from fwm.mkgen.makefile import generate_record, generate_create_checkpoints


@click.group(cls=SectionedGroup, short_help="Generate and check the runs that make traces and checkpoints.",
             sections=(("Generating the runs", ("mkgen",)), ("Checking their outputs", ("check-fesvr-trace",))))
def bootstrap():
    """
    Generate and check the runs that make the repository's traces and checkpoints.

    These commands only read the repository: `fwm repo compose` imports their results. The fwm-bootstrap pipeline
    runs them for every app of a suite. The examples are its steps for one entry point E, with run directories R1
    to R3.

    DESC lists the checkpoints to create, and S is their SimPoint information: both come from a SimPoint analysis of
    R1's basic block vectors (BBVs). V is the output of `spike --version`.

    \b
    Examples:
      # 1. Record a run of E, with BBVs (-s) for SimPoint
      fwm repo bootstrap mkgen record-fesvr-trace E -o R1
      make -C R1 run SIM_FLAGS_EXTRA=-s<interval>

    \b
      # 2. Check the recording
      fwm repo bootstrap check-fesvr-trace R1/trace \\
        --final-state R1/final-state.json

    \b
      # 3. Import it as a trace: prints its UUID
      fwm repo compose add trace E R1/trace \\
        --final-state R1/final-state.json

    \b
      # 4. Create the checkpoints of DESC
      fwm repo bootstrap mkgen create-checkpoints UUID DESC -o R2
      make -C R2 run

    \b
      # 5. Import them
      fwm repo compose add checkpoint UUID R2/ckpt.desc R2/checkpoints \\
        --simpoint-info S --sim-version V

    \b
      # 6. Run each checkpoint to the end
      fwm mkgen from-checkpoint UUID:NAME -o R3
      make -C R3 run

    \b
      # 7. Record whether the run validates the checkpoint
      fwm repo compose set-checkpoint-validation UUID:NAME R3
    """


@bootstrap.group(cls=SectionedGroup, short_help="Generate a run that records a trace or makes checkpoints.",
                 sections=(("Runs, in the pipeline's order", ("record-fesvr-trace", "create-checkpoints")),))
def mkgen():
    """
    Generate the runs that record traces and create checkpoints.

    Like `fwm mkgen`, each writes a run directory, to run with `make -C DIR run`. Neither writes to the repository.

    \b
    Examples:
      # Generate a record run, and run it
      fwm repo bootstrap mkgen record-fesvr-trace bfs-road-n64 -o run1
      make -C run1 run
    """


@mkgen.command("record-fesvr-trace", short_help="A run that records a trace of an entry point.")
@click.argument("entry", shell_complete=complete_entry)
@out_dir_option
@click.pass_context
def record_fesvr_trace(ctx, entry, out_dir):
    """
    Generate a run that records a trace of an entry point, in a fresh copy of its sysroot. Linux only.

    The run writes trace/ (the recording), final-state.json, and the program's console in console.stdout and
    console.stderr. Then check the trace, and import it, as in the examples.

    \b
    Examples:
      # Record a run, with BBVs for SimPoint
      fwm repo bootstrap mkgen record-fesvr-trace bfs-road-n64 -o run1
      make -C run1 run SIM_FLAGS_EXTRA=-s100000000

    \b
      # Check the trace, then import it
      fwm repo bootstrap check-fesvr-trace run1/trace \\
        --final-state run1/final-state.json
      fwm repo compose add trace bfs-road-n64 run1/trace \\
        --final-state run1/final-state.json
    """
    done(generate_record(open_repo(ctx), entry, out_dir))


@mkgen.command("create-checkpoints", short_help="A run that creates the checkpoints of a description.")
@click.argument("trace", shell_complete=complete_trace)
@click.argument("desc", type=click.Path(exists=True, dir_okay=False))
@out_dir_option
@click.pass_context
def create_checkpoints(ctx, trace, desc, out_dir):
    """
    Generate a run that replays TRACE and creates the checkpoints of DESC.

    DESC lists the checkpoints, one "NAME: INSTRUCTION_COUNT" line each. The run writes ckpt.desc (a copy of DESC),
    checkpoints/ and final-state.json.

    \b
    Examples:
      # Create the checkpoints of ckpts.txt, then import them
      fwm repo bootstrap mkgen create-checkpoints bfs-road-n64 ckpts.txt -o run2
      make -C run2 run
      fwm repo compose add checkpoint bfs-road-n64 run2/ckpt.desc run2/checkpoints
    """
    done(generate_create_checkpoints(open_repo(ctx), trace, desc, out_dir))


@bootstrap.command("check-fesvr-trace", short_help="Check a recorded trace before importing it.")
@click.argument("recording_dir", type=click.Path(file_okay=False))
@click.option("--final-state", type=click.Path(dir_okay=False),
              help="The run's final state (final-state.json).")
@click.option("--json", "as_json", is_flag=True,
              help="Print JSON, for scripts.")
def check_fesvr_trace(recording_dir, final_state, as_json):
    """
    Check a recording before importing it, as `fwm repo compose add trace` will.

    It needs no repository: run it on the node that made the recording. It passes when the recording is complete and
    well formed, and, with --final-state, when the run ended with the program's exit. It prints the exit status and
    each processor's (HART's) instruction count.

    It fails with status 4 otherwise, e.g. for a run stopped by a job's time limit, which is worth retrying with more
    time.

    \b
    Examples:
      # Check the trace of a record run, on the node that ran it
      fwm repo bootstrap check-fesvr-trace run1/trace \\
        --final-state run1/final-state.json
    """
    # imported here: it loads pydantic, which completing `fwm repo ...` must not
    from fwm.repo.trace import check_recording

    recording, state = check_recording(recording_dir, final_state)
    instrets = state.instrets if state is not None else None
    harts = [{"hart_id": h.hart_id, "recording_sha256": h.recording_sha256, "num_commands": h.num_commands,
              "instret": instrets[h.hart_id] if instrets is not None else None} for h in recording.harts]
    if as_json:
        spec = recording.target_spec
        echo_json({"complete": True, "exit_status": recording.exit_status, "mem_mb": spec.mem_mb,
                   "num_harts": spec.num_harts, "boot_elf_sha256": spec.boot_elf_sha256, "harts": harts})
    else:
        instret = "unknown (no final state)" if instrets is None else str(instrets)
        click.echo(f"complete: the program exited with status {recording.exit_status}; instret {instret}")
