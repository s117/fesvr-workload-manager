import hashlib
import json
import os
import re

import pytest
from click.testing import CliRunner

from fwm.cli.fwm import cli
from tests.fesvr_files import final_state_json, write_recording, recording_sha256, Command, exit_command, DEFAULT_BOOT_ELF

COMMANDS = [Command(), exit_command(3)]


def check(*args):
    result = CliRunner().invoke(cli, ["repo", "bootstrap", "check-fesvr-trace", *args])
    return result.exit_code, result.stdout, result.stderr


@pytest.fixture
def no_repo(monkeypatch):
    """`fwm repo bootstrap check-fesvr-trace` needs no repository."""
    monkeypatch.delenv("FWM_REPO_PATH", raising=False)


def write_final_state(tmp_path, *instrets, exit_code=0):
    path = tmp_path / "final-state.json"
    path.write_text(final_state_json(*instrets, exit_code=exit_code))
    return str(path)


def test_complete(tmp_path, no_repo):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    code, out, _ = check(str(rec), "--final-state", write_final_state(tmp_path, 5000, exit_code=3), "--json")
    assert code == 0
    assert json.loads(out) == {
        "complete": True, "exit_status": 3, "mem_mb": 1, "num_harts": 1,
        "boot_elf_sha256": hashlib.sha256(DEFAULT_BOOT_ELF).hexdigest(),
        "harts": [{"hart_id": 0, "recording_sha256": recording_sha256(COMMANDS), "num_commands": 2, "instret": 5000}]}

    code, out, _ = check(str(rec), "--final-state", write_final_state(tmp_path, 5000, exit_code=3))
    assert code == 0
    assert out == "complete: the program exited with status 3; instret [5000]\n"


def test_without_final_state(tmp_path, no_repo):
    rec = tmp_path / "rec"
    write_recording(str(rec))
    code, out, _ = check(str(rec), "--json")
    assert code == 0
    assert json.loads(out)["harts"][0]["instret"] is None
    code, out, _ = check(str(rec))
    assert "instret unknown" in out


def test_truncated(tmp_path, no_repo):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[[Command(), Command()]])
    code, out, err = check(str(rec))
    assert (code, out) == (4, "")
    assert "is truncated" in err


@pytest.mark.parametrize("final_state, match", [
    # spike stopped by SIGTERM (e.g. a job's time limit) says so, and exits 0
    (final_state_json(1234, reason="interrupted", signal="SIGTERM"), "SIGTERM stopped it .* record it again"),
    (final_state_json(1234, reason="instret_limit"), "-e.* import it without --final-state"),
    (final_state_json(reason="error", error="unknown syscall 220"), "the simulator failed: unknown syscall 220"),
    (final_state_json(reason="unfinished"), "spike died"),
    (final_state_json(1234, reason="checkpoints_created"), "isn't the final state of a record run"),
])
def test_run_that_didnt_exit(tmp_path, no_repo, final_state, match):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[[Command(), Command()]])
    path = tmp_path / "final-state.json"
    path.write_text(final_state)
    code, _, err = check(str(rec), "--final-state", str(path))
    assert code == 4
    assert re.search(match, err)


@pytest.mark.parametrize("final_state, match", [
    ("", "is empty: it was written by a spike older than"),
    ('{"hart_state": [{"instret": 1}]}', "has no finish_reason"),
    (final_state_json(1, 2), "2 HART"),
    (final_state_json(1, exit_code=3), "exited with code 3, but the recording .* with status 0"),
    ("{", "is not JSON"),
])
def test_bad_final_state(tmp_path, no_repo, final_state, match):
    rec = tmp_path / "rec"
    write_recording(str(rec))
    path = tmp_path / "final-state.json"
    path.write_text(final_state)
    code, _, err = check(str(rec), "--final-state", str(path))
    assert code == 4
    assert re.search(match, err)


def test_malformed(tmp_path, no_repo):
    rec = tmp_path / "rec"
    write_recording(str(rec))
    os.remove(rec / "boot.elf")
    code, _, err = check(str(rec))
    assert code == 4
    assert "has no boot.elf" in err


def test_not_a_directory(tmp_path, no_repo):
    code, _, err = check(str(tmp_path / "none"))
    assert code == 3
    assert "is not a directory" in err


def test_exit_without_one_in_the_recording(tmp_path, no_repo):
    """A final state saying the program exited, with a recording that never did: not the same run."""
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[[Command(), Command()]])
    code, _, err = check(str(rec), "--final-state", write_final_state(tmp_path, 1234))
    assert code == 4
    assert "says the program exited, but the recording" in err
