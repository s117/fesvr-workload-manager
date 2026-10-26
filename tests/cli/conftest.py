import json

import pytest
from click.testing import CliRunner

from fwm.cli.fwm import cli
from tests.fesvr_files import (
    final_state_json,
    write_recording, write_checkpoint, Command, exit_command, recording_sha256, CheckpointHart
)

COMMANDS = [Command(), exit_command(0)]


@pytest.fixture
def run(tmp_path):
    """Run fwm on a new repository; return (exit code, stdout, stderr)."""
    repo_path = str(tmp_path / "repo")
    runner = CliRunner()

    def invoke(*args, repo=True):
        result = runner.invoke(cli, (["--repo-path", repo_path] if repo else []) + list(args))
        if result.exception and not isinstance(result.exception, SystemExit):
            raise result.exception
        return result.exit_code, result.stdout, result.stderr

    assert invoke("repo", "init", repo_path, repo=False) == (0, repo_path + "\n", "")
    return invoke


@pytest.fixture
def populated(run, sysroot_src, write_entry_point, tmp_path):
    """A repository with a sysroot, an entry point, a trace and a checkpoint; return the trace UUID."""
    assert run("repo", "compose", "add", "sysroot", str(sysroot_src), "sr")[:2] == (0, "sr\n")
    assert run("repo", "compose", "add", "entry", "sr", "wl", "e", write_entry_point(), "-d", "an entry")[:2] == (0, "e\n")
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    final_state = tmp_path / "final-state.json"
    final_state.write_text(final_state_json(5000))
    code, out, _ = run("repo", "compose", "add", "trace", "e", str(rec), "--final-state", str(final_state))
    assert code == 0
    trace_uuid = out.strip()
    assert run("repo", "compose", "add", "checkpoint", trace_uuid[:8], *write_checkpoints(tmp_path / "ckpt-run", "e.2.0.40"),
               "--simpoint-info", str(write_simpoint_info(tmp_path, {"e.2.0.40": (2, 0.4)})))[:2] == \
        (0, f"{trace_uuid}:e.2.0.40\n")
    return trace_uuid


def write_checkpoints(run_dir, *names, commands=COMMANDS):
    """Checkpoints as a create-checkpoints run leaves them, each taken at 1000 instructions: return the paths of the
    description file and of the checkpoint directory."""
    checkpoints = run_dir / "checkpoints"
    checkpoints.mkdir(parents=True)
    for name in names:
        write_checkpoint(str(checkpoints / name), [CheckpointHart(recording_sha256(commands), cmd_index=1)])
    desc = run_dir / "ckpt.desc"
    desc.write_text("".join(f"{name}: 1000\n" for name in names))
    return str(desc), str(checkpoints)


def write_simpoint_info(directory, checkpoints, interval=1000):
    """A SimPoint information file of checkpoints {name: (index, weight)}."""
    path = directory / "simpoints.json"
    path.write_text(json.dumps({"interval": interval, "maxK": 5, "checkpoints": {
        name: {"index": index, "weight": weight} for name, (index, weight) in checkpoints.items()}}))
    return path
