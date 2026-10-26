import click

from fwm.cli.output import echo_json
from fwm.repo.trace import check_recording


@click.group()
def check():
    """
    Check the outputs of a run before importing them, without a repository: e.g. on the node that ran it.
    """


@check.command("recording")
@click.argument("recording_dir", type=click.Path(file_okay=False))
@click.option("--final-state", type=click.Path(dir_okay=False),
              help="The +final-state-dump of the recording run: it must say the program exited, as the recording "
                   "does, and gives the instret of each HART.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON, for scripts.")
def check_recording_cmd(recording_dir, final_state, as_json):
    """
    Check RECORDING_DIR, a recording (+dev-traffic-record), as `fwm repo compose add trace` would: it must be complete (the
    program exited) and well formed, and agree with the final state, if one is given, which must say the program
    exited (finish_reason target_exit).

    \b
    Prints the exit status and each HART's instruction count; with --json, an object of the trace's fields:
      {"complete", "exit_status", "mem_mb", "num_harts", "boot_elf_sha256",
       "harts": [{"hart_id", "recording_sha256", "num_commands", "instret"}]}
    Fails with status 4 if the recording is truncated, malformed, or disagrees with the final state, or if the final
    state says the run ended otherwise (e.g. "interrupted" by a job's time limit, or an "error" of the simulator).
    """
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
