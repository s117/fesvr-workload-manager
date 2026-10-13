import json
import os

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


def test_no_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("FWM_REPO_PATH", raising=False)
    result = CliRunner().invoke(cli, ["list"])
    assert result.exit_code == 2
    assert "no workload repository" in result.stderr


def test_not_a_repo(tmp_path):
    result = CliRunner().invoke(cli, ["--repo-path", str(tmp_path), "list"])
    assert result.exit_code == 3
    assert "is not an fwm repository" in result.stderr


def test_repo_path_from_env(run, tmp_path, monkeypatch):
    monkeypatch.setenv("FWM_REPO_PATH", str(tmp_path / "repo"))
    result = CliRunner().invoke(cli, ["list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_list_and_show(run, populated):
    trace_uuid = populated

    code, out, _ = run("list", "entries", "--json")
    assert code == 0
    entries = json.loads(out)
    assert [(e["name"], e["workload"], e["sysroot"], e["traces"]) for e in entries] == [("e", "wl", "sr", [trace_uuid])]

    code, out, _ = run("list", "traces", "--json")
    traces = json.loads(out)
    assert [(t["uuid"], t["complete"], t["exit_status"], t["harts"][0]["instret"], t["checkpoints"])
            for t in traces] == [(trace_uuid, True, 0, 5000, ["e.2.0.40"])]

    code, out, _ = run("list", "checkpoints", "--json", "--trace", "e")
    ckpts = json.loads(out)
    assert [(c["name"], c["trace"], c["simpoint_weight"], c["harts"][0]["csr_count"]) for c in ckpts] == [
        ("e.2.0.40", trace_uuid, 0.4, 1000)]

    code, out, _ = run("show", "entry", "e", "--json")
    assert json.loads(out)["description"] == "an entry"
    code, out, _ = run("show", "sysroot", "sr", "--json")
    assert json.loads(out)["entries"] == ["e"]
    code, out, _ = run("show", "workload", "wl", "--json")
    assert json.loads(out)["entries"] == ["e"]
    code, out, _ = run("show", "trace", "e", "--json")
    assert json.loads(out)["uuid"] == trace_uuid
    code, out, _ = run("show", "checkpoint", "e:e.2.0.40", "--json")
    assert json.loads(out)["path"].endswith(os.path.join(trace_uuid, "e.2.0.40"))


def test_list_tables(run, populated):
    for kind in ("workloads", "sysroots", "entries", "traces", "checkpoints"):
        code, out, _ = run("list", kind)
        assert code == 0
        assert len(out.splitlines()) == 2  # heading and one row
    code, out, _ = run("show", "trace", "e")
    assert "num_commands: 2" in out


def test_list_filters(run, populated, write_entry_point):
    run("repo", "add", "entry", "sr", "other-wl", "other", write_entry_point())
    _, out, _ = run("list", "entries", "--workload", "wl", "--json")
    assert [e["name"] for e in json.loads(out)] == ["e"]
    _, out, _ = run("list", "entries", "oth", "--json")
    assert [e["name"] for e in json.loads(out)] == ["other"]
    assert run("list", "traces", "--workload", "wl")[0] == 2


def test_exit_codes(run, populated, write_entry_point, tmp_path):
    # not found
    code, _, err = run("show", "entry", "missing")
    assert code == 3
    assert "error: no entry point named 'missing'" in err
    # failed check
    code, _, err = run("repo", "add", "entry", "sr", "wl", "e", write_entry_point())
    assert code == 4
    assert "already exists" in err
    code, _, err = run("repo", "remove", "sysroot", "sr")
    assert code == 4
    assert "--recursive" in err
    # usage error
    assert run("repo", "remove", "nothing", "x")[0] == 2


def test_verify(run, populated, tmp_path):
    assert run("repo", "verify")[0] == 0
    assert run("repo", "verify", "trace", "e")[0] == 0
    assert run("repo", "verify", "trace")[0] == 2

    path = tmp_path / "repo" / "sysroots" / "sr" / "app" / "input.txt"
    path.chmod(0o644)
    path.write_text("tampered")
    code, out, err = run("repo", "verify")
    assert code == 4
    assert out.startswith("sysroot sr: app/input.txt: file size mismatch")
    assert "1 item(s) failed" in err


def test_remove(run, populated, tmp_path):
    trace_uuid = populated
    assert run("repo", "remove", "checkpoint", "e.2.0.40")[0] == 0
    assert run("repo", "remove", "sysroot", "sr", "--recursive")[0] == 0
    for kind in ("workloads", "sysroots", "entries", "traces", "checkpoints"):
        assert json.loads(run("list", kind, "--json")[1]) == []
    assert not (tmp_path / "repo" / "traces" / trace_uuid).exists()


def test_spawn(run, populated, tmp_path):
    dest = tmp_path / "copy"
    assert run("spawn", "sr", str(dest))[0] == 0
    assert (dest / "app" / "prog.rv64").is_file()
    assert run("spawn", "sr", str(dest))[0] == 4
    assert run("spawn", "--force", "sr", str(dest))[0] == 0


def test_mkgen(run, populated, tmp_path, caplog):
    code, out, _ = run("mkgen", "record", "e", "-o", str(tmp_path / "r1"))
    assert (code, out) == (0, str(tmp_path / "r1" / "Makefile") + "\n")
    assert f"make -C {tmp_path / 'r1'} run" in caplog.text

    assert run("mkgen", "replay", "e", "-o", str(tmp_path / "r2"))[0] == 0
    desc = tmp_path / "d.txt"
    desc.write_text("c: 100\n")
    assert run("mkgen", "replay", "e", "--create-checkpoints", str(desc), "-o", str(tmp_path / "r3"))[0] == 0
    assert (tmp_path / "r3" / "ckpt.desc").is_file()
    assert run("mkgen", "load", "e.2.0.40", "-o", str(tmp_path / "r4"))[0] == 0

    desc.write_text("c: 9000\n")
    code, _, err = run("mkgen", "replay", "e", "--create-checkpoints", str(desc), "-o", str(tmp_path / "r5"))
    assert code == 4
    assert "stops after 5000 instructions" in err
