import pytest
from click.testing import CliRunner

from fwm.cli.fwm import cli
from tests.fesvr_files import (
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
    assert run("repo", "add", "sysroot", str(sysroot_src), "sr")[:2] == (0, "sr\n")
    assert run("repo", "add", "entry", "sr", "wl", "e", write_entry_point(), "-d", "an entry")[:2] == (0, "e\n")
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    final_state = tmp_path / "final-state.json"
    final_state.write_text('{"hart_state": [{"instret": 5000}]}')
    code, out, _ = run("repo", "add", "trace", "e", str(rec), "--final-state", str(final_state))
    assert code == 0
    trace_uuid = out.strip()
    ckpt = tmp_path / "e.2.0.40"
    write_checkpoint(str(ckpt), [CheckpointHart(recording_sha256(COMMANDS), cmd_index=1)])
    assert run("repo", "add", "checkpoint", trace_uuid[:8], str(ckpt))[:2] == (0, f"{trace_uuid}:e.2.0.40\n")
    return trace_uuid
