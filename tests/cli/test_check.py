import hashlib
import json
import os

import pytest
from click.testing import CliRunner

from fwm.cli.fwm import cli
from tests.fesvr_files import write_recording, recording_sha256, Command, exit_command, DEFAULT_BOOT_ELF

COMMANDS = [Command(), exit_command(3)]


def check(*args):
    result = CliRunner().invoke(cli, ["check", "recording", *args])
    return result.exit_code, result.stdout, result.stderr


@pytest.fixture
def no_repo(monkeypatch):
    """`fwm check` needs no repository."""
    monkeypatch.delenv("FWM_REPO_PATH", raising=False)


def write_final_state(tmp_path, *instrets):
    path = tmp_path / "final-state.json"
    path.write_text(json.dumps({"hart_state": [{"instret": i} for i in instrets]}))
    return str(path)


def test_complete(tmp_path, no_repo):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    code, out, _ = check(str(rec), "--final-state", write_final_state(tmp_path, 5000), "--json")
    assert code == 0
    assert json.loads(out) == {
        "complete": True, "exit_status": 3, "mem_mb": 1, "num_harts": 1,
        "boot_elf_sha256": hashlib.sha256(DEFAULT_BOOT_ELF).hexdigest(),
        "harts": [{"hart_id": 0, "recording_sha256": recording_sha256(COMMANDS), "num_commands": 2, "instret": 5000}]}

    code, out, _ = check(str(rec), "--final-state", write_final_state(tmp_path, 5000))
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


def test_cut_short_by_a_signal(tmp_path, no_repo):
    """spike stopped by SIGTERM (e.g. a job's time limit) writes its final state and exits 0."""
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[[Command(), Command()]])
    code, _, err = check(str(rec), "--final-state", write_final_state(tmp_path, 1234))
    assert code == 4
    assert "stopped by a signal" in err


@pytest.mark.parametrize("final_state, match", [
    ("", "is empty"),
    ('{"hart_state": [{"instret": 1}, {"instret": 2}]}', "2 HART"),
    ("{", "invalid final state"),
])
def test_bad_final_state(tmp_path, no_repo, final_state, match):
    rec = tmp_path / "rec"
    write_recording(str(rec))
    path = tmp_path / "final-state.json"
    path.write_text(final_state)
    code, _, err = check(str(rec), "--final-state", str(path))
    assert code == 4
    assert match in err


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
