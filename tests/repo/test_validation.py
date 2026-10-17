import json
import re

import pytest
from click.testing import CliRunner

from fwm.cli.fwm import cli
from fwm.database.schema import DecoupledCheckpoint
from fwm.mkgen.makefile import generate_from_checkpoint, generate_from_fesvr_trace
from fwm.repo.entry import add_entry
from fwm.repo.errors import FwmValidationError
from fwm.repo.sysroot import add_sysroot
from fwm.repo.trace import add_trace
from fwm.repo.validation import record_validation, PASSED, FAILED
from tests.conftest import import_checkpoint
from tests.fesvr_files import final_state_json, write_recording, write_checkpoint, Command, exit_command, recording_sha256, CheckpointHart

COMMANDS = [Command(), exit_command(0)]


@pytest.fixture
def ckpt(repo, sysroot_src, write_entry_point, tmp_path):
    add_sysroot(repo, str(sysroot_src), "sr")
    add_entry(repo, "sr", "wl", "e", write_entry_point())
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    final_state = tmp_path / "final-state.json"
    final_state.write_text(final_state_json(5000))
    add_trace(repo, "e", str(rec), final_state=str(final_state))
    for name in ("c", "d"):
        write_checkpoint(str(tmp_path / name), [CheckpointHart(recording_sha256(COMMANDS), cmd_index=1)])
        import_checkpoint(repo, "e", str(tmp_path / name))
    return "c"


@pytest.fixture
def load_run(repo, ckpt, tmp_path):
    run = tmp_path / "load"
    generate_from_checkpoint(repo, ckpt, str(run))
    return run


def final_state(run, instret):
    (run / "final-state.json").write_text(final_state_json(instret))


def test_passed(repo, load_run):
    final_state(load_run, 5000)
    ckpt = record_validation(repo, "c", str(load_run))
    assert ckpt.validation_status == PASSED
    stored = DecoupledCheckpoint.get_by_id(ckpt.id)
    assert stored.validation_status == PASSED
    assert stored.validation_time is not None
    assert "5000" in stored.validation_detail


@pytest.mark.parametrize("content, reason", [
    (None, "has no final-state.json"),
    ("", "is empty: it was written by a spike older than"),
    ('{"hart_state": [{"instret": 5000}]}', "has no finish_reason"),
    (final_state_json(4999), r"ended at instret \[4999\], the trace at \[5000\]"),
    (final_state_json(5000, exit_code=1), "exited with code 1, in the trace with status 0"),
    (final_state_json(reason="error", error="the replay diverged"), "didn't end with the program's exit: the "
                                                                    "simulator failed: the replay diverged"),
    (final_state_json(4000, reason="interrupted", signal="SIGTERM"), r"SIGTERM stopped it .* at instret \[4000\]"),
    (final_state_json(100, reason="instret_limit"), r"-e.* at instret \[100\]"),
    (final_state_json(reason="unfinished"), "spike died"),
    ("{", "not JSON"),
])
def test_failed(repo, load_run, content, reason):
    if content is not None:
        (load_run / "final-state.json").write_text(content)
    ckpt = record_validation(repo, "c", str(load_run))
    assert ckpt.validation_status == FAILED
    assert re.search(reason, ckpt.validation_detail)


def test_revalidation_overwrites(repo, load_run):
    final_state(load_run, 1)
    assert record_validation(repo, "c", str(load_run)).validation_status == FAILED
    final_state(load_run, 5000)
    assert record_validation(repo, "c", str(load_run)).validation_status == PASSED


def test_wrong_run_dir(repo, ckpt, tmp_path):
    other = tmp_path / "other"
    generate_from_checkpoint(repo, "d", str(other))
    with pytest.raises(FwmValidationError, match="not a run directory of checkpoint"):
        record_validation(repo, "c", str(other))
    replay = tmp_path / "replay"
    generate_from_fesvr_trace(repo, "e", str(replay))
    with pytest.raises(FwmValidationError, match="not a run directory of checkpoint"):
        record_validation(repo, "c", str(replay))


def test_trace_without_instret(repo, sysroot_src, write_entry_point, tmp_path):
    add_sysroot(repo, str(sysroot_src), "sr")
    add_entry(repo, "sr", "wl", "e", write_entry_point())
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    add_trace(repo, "e", str(rec))
    write_checkpoint(str(tmp_path / "c"), [CheckpointHart(recording_sha256(COMMANDS), cmd_index=1)])
    import_checkpoint(repo, "e", str(tmp_path / "c"))
    run = tmp_path / "load"
    generate_from_checkpoint(repo, "c", str(run))
    final_state(run, 5000)
    with pytest.raises(FwmValidationError, match="no final instruction count"):
        record_validation(repo, "c", str(run))


def test_cli(repo, load_run):
    runner = CliRunner()
    final_state(load_run, 5000)
    result = runner.invoke(cli, ["--repo-path", repo.path, "repo", "record-validation", "c", str(load_run)])
    assert result.exit_code == 0
    assert result.stdout.startswith("passed: ")

    (load_run / "final-state.json").write_text(final_state_json(reason="error", error="the replay diverged"))
    result = runner.invoke(cli, ["--repo-path", repo.path, "repo", "record-validation", "c", str(load_run)])
    assert result.exit_code == 4
    assert result.stdout.startswith("failed: the run didn't end with the program's exit: the simulator failed")

    result = runner.invoke(cli, ["--repo-path", repo.path, "show", "checkpoint", "c", "--json"])
    assert json.loads(result.stdout)["validation"]["status"] == "failed"
