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


@click.group(cls=SectionedGroup, short_help="Generate the runs that make traces and checkpoints; check them.",
             sections=(("Generating the runs", ("mkgen",)), ("Checking their outputs", ("check-fesvr-trace",))))
def bootstrap():
    """
    Make the repository's traces and checkpoints, for its creator. These commands only read the repository: they
    generate the runs and check their outputs, and `fwm repo compose` imports the results.

    \b
    The steps, for an entry point ENTRY:
      1. fwm repo bootstrap mkgen record-fesvr-trace ENTRY -o R1; make -C R1 run
      2. fwm repo bootstrap check-fesvr-trace R1/trace --final-state R1/final-state.json
      3. fwm repo compose add trace ENTRY R1/trace --final-state R1/final-state.json  -> its UUID
      4. fwm repo bootstrap mkgen create-checkpoints UUID DESC -o R2; make -C R2 run
      5. fwm repo compose add checkpoint UUID R2/ckpt.desc R2/checkpoints
      6. fwm mkgen from-checkpoint UUID:NAME -o R3; make -C R3 run  (to the end of the program)
      7. fwm repo compose set-checkpoint-validation UUID:NAME R3
    """


@bootstrap.group(cls=SectionedGroup, short_help="Generate a run that records a trace or makes checkpoints.",
                 sections=(("Runs, in the pipeline's order", ("record-fesvr-trace", "create-checkpoints")),))
def mkgen():
    """
    Generate the runs that record the repository's traces and create its checkpoints.

    Like `fwm mkgen`, each generates a run directory (a Makefile and run.json) to run with `make -C <dir> run`, and
    only reads the repository.
    """


@mkgen.command("record-fesvr-trace", short_help="A run that records a trace of an entry point.")
@click.argument("entry", shell_complete=complete_entry)
@out_dir_option
@click.pass_context
def record_fesvr_trace(ctx, entry, out_dir):
    """
    Record a run of the entry point ENTRY in a fresh copy of its sysroot: its FESVR trace (+dev-traffic-record).

    Outputs trace/, final-state.json, console.stdout and console.stderr.

    \b
    Check the trace, then import it, with
      fwm repo bootstrap check-fesvr-trace <dir>/trace --final-state <dir>/final-state.json
      fwm repo compose add trace ENTRY <dir>/trace --final-state <dir>/final-state.json
    """
    done(generate_record(open_repo(ctx), entry, out_dir))


@mkgen.command("create-checkpoints", short_help="A run that creates the checkpoints of a description.")
@click.argument("trace", shell_complete=complete_trace)
@click.argument("desc", type=click.Path(exists=True, dir_okay=False))
@out_dir_option
@click.pass_context
def create_checkpoints(ctx, trace, desc, out_dir):
    """
    Replay TRACE (a trace UUID or prefix, or an entry point with one trace), and create the checkpoints of DESC, a
    description file of "<name>: <instruction count>" lines.

    Outputs ckpt.desc (a copy of DESC), checkpoints/ and final-state.json.

    \b
    Import the checkpoints with
      fwm repo compose add checkpoint TRACE <dir>/ckpt.desc <dir>/checkpoints
    """
    done(generate_create_checkpoints(open_repo(ctx), trace, desc, out_dir))


@bootstrap.command("check-fesvr-trace", short_help="Check a recorded trace before importing it.")
@click.argument("recording_dir", type=click.Path(file_okay=False))
@click.option("--final-state", type=click.Path(dir_okay=False),
              help="The +final-state-dump of the recording run: it must say the program exited, as the recording "
                   "does, and gives the instret of each HART.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON, for scripts.")
def check_fesvr_trace(recording_dir, final_state, as_json):
    """
    Check RECORDING_DIR, a recording (+dev-traffic-record), as `fwm repo compose add trace` would: it must be complete
    (the program exited) and well formed, and agree with the final state, if one is given, which must say the program
    exited (finish_reason target_exit). It needs no repository: run it on the node that made the recording.

    \b
    Prints the exit status and each HART's instruction count; with --json, an object of the trace's fields:
      {"complete", "exit_status", "mem_mb", "num_harts", "boot_elf_sha256",
       "harts": [{"hart_id", "recording_sha256", "num_commands", "instret"}]}
    Fails with status 4 if the recording is truncated, malformed, or disagrees with the final state, or if the final
    state says the run ended otherwise (e.g. "interrupted" by a job's time limit, or an "error" of the simulator).
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
