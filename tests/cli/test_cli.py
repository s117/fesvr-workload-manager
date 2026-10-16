import json
import os

from click.testing import CliRunner

from fwm.cli.fwm import cli
from tests.data_loader.test_sim_version import SPIKE_VERSION
from tests.cli.conftest import COMMANDS
from tests.fesvr_files import (
    write_recording, write_checkpoint, recording_sha256, Command, CheckpointHart, encode_hart_state,
    default_hart_state_entries, exit_command
)


def test_no_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("FWM_REPO_PATH", raising=False)
    result = CliRunner().invoke(cli, ["list"])
    assert result.exit_code == 2
    assert "no workload repository" in result.stderr


def test_not_a_repo(tmp_path):
    result = CliRunner().invoke(cli, ["--repo-path", str(tmp_path), "list"])
    assert result.exit_code == 2
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


def test_metadata(run, populated, sysroot_src, write_entry_point, tmp_path):
    metadata = {"suite": "Toolchain test", "description": "A helloworld.", "language": ["C"],
                "auxiliary": {"validator": {"verify": [{"cmd": "diff a b", "stdout": "a.cmp"}]}}}
    assert run("repo", "add", "entry", "sr", "wl", "m", write_entry_point(metadata=metadata))[0] == 0
    assert json.loads(run("show", "entry", "m", "--json")[1])["metadata"] == metadata
    code, out, _ = run("show", "entry", "m")
    assert code == 0
    assert "metadata:\n  suite: Toolchain test\n" in out
    assert "language: C\n" in out
    assert "cmd: diff a b" in out
    assert json.loads(run("show", "entry", "e", "--json")[1])["metadata"] is None

    # replaced, and removed, keeping the traces
    new = tmp_path / "metadata.json"
    new.write_text('{"suite": "GAPBS"}')
    assert run("repo", "set-metadata", "e", str(new))[:2] == (0, "e\n")
    view = json.loads(run("show", "entry", "e", "--json")[1])
    assert (view["metadata"], view["traces"]) == ({"suite": "GAPBS"}, [populated])
    new.write_text("null")
    assert run("repo", "set-metadata", "e", str(new))[0] == 0
    assert json.loads(run("show", "entry", "e", "--json")[1])["metadata"] is None

    for bad in ('["GAPBS"]', '{"suite": '):
        new.write_text(bad)
        code, _, err = run("repo", "set-metadata", "e", str(new))
        assert code == 4, err
    new.write_text("{}")
    assert run("repo", "set-metadata", "missing", str(new))[0] == 3


def test_exit_codes(run, populated, write_entry_point, tmp_path):
    # not found
    code, _, err = run("show", "entry", "missing")
    assert code == 3
    assert "error: no entry point named 'missing'" in err
    # already exists
    code, _, err = run("repo", "add", "entry", "sr", "wl", "e", write_entry_point(command="other"))
    assert code == 5
    assert "already exists" in err
    # failed check
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
    assert "exits after 5000 instructions" in err


def test_read_only_repo(run, populated, tmp_path, write_entry_point):
    """A repository the user can't write to can still be read: list, show, verify, mkgen."""
    repo_path = tmp_path / "repo"
    readonly = [repo_path, repo_path / "fwm.db", repo_path / ".staging"]
    for p in readonly:
        p.chmod(p.stat().st_mode & ~0o222)
    try:
        assert run("list", "--json")[0] == 0
        assert run("show", "trace", populated, "--json")[0] == 0
        assert run("repo", "verify")[0] == 0
        assert run("mkgen", "replay", populated, "-o", str(tmp_path / "run"))[0] == 0
        assert json.loads((tmp_path / "run" / "run.json").read_text())["trace"]["uuid"] == populated
        # no journal or other file is left behind by reading
        assert sorted(os.listdir(repo_path)) == [".staging", "checkpoints", "fwm.db", "sysroots", "traces"]

        code, out, err = run("repo", "add", "entry", "sr", "wl", "e2", write_entry_point())
        assert (code, out) == (2, "")
        assert "is read-only for you" in err
    finally:
        for p in readonly:
            p.chmod(p.stat().st_mode | 0o200)


def test_sim_version(run, populated, tmp_path):
    version = tmp_path / "sim-version.txt"
    version.write_text(SPIKE_VERSION)
    rec = tmp_path / "rec2"
    write_recording(str(rec), harts=[[Command(payload=0x3000), exit_command(0)]])
    code, out, _ = run("repo", "add", "trace", "e", str(rec), "--sim-version", str(version))
    assert code == 0
    trace_uuid = out.strip()

    code, out, _ = run("show", "trace", trace_uuid, "--json")
    view = json.loads(out)
    assert view["sim_version"]["raw"] == SPIKE_VERSION
    assert view["sim_version"]["builds"]["spike"]["Commit"] == "0d7a85c80db593a638d33cb655d866b174d69bde"
    assert view["fwm_version"]
    code, out, _ = run("show", "trace", trace_uuid)
    assert "spike: heads/anycore-gcc_16.1.0-llvm_22.1.8-0-g0d7a85c (0d7a85c80db5)" in out
    assert "FESVR (libfesvr): heads/anycore-gcc_16.1.0-llvm_22.1.8-0-g4fce14b (4fce14b1b852)" in out

    # a trace imported without one
    assert json.loads(run("show", "trace", populated, "--json")[1])["sim_version"] is None

    version.write_text("not a version\n")
    rec3 = tmp_path / "rec3"
    write_recording(str(rec3), harts=[[Command(payload=0x4000), exit_command(0)]])
    code, out, err = run("repo", "add", "trace", "e", str(rec3), "--sim-version", str(version))
    assert (code, out) == (4, "")
    assert "invalid --sim-version" in err


def test_add_checkpoints(run, populated, tmp_path):
    files = []
    for name, count in (("e.5.0.25", 3000), ("e.7.0.35", 4000)):
        path = tmp_path / name
        write_checkpoint(str(path), [CheckpointHart(recording_sha256(COMMANDS), cmd_index=1,
                                                    state=encode_hart_state(default_hart_state_entries(count=count)))])
        files.append(str(path))
    desc = tmp_path / "ckpt.desc"
    desc.write_text("e.5.0.25: 3000\ne.7.0.35: 4000\n")
    info = tmp_path / "simpoints.json"
    info.write_text(json.dumps({"e.5.0.25": {"simpoint_index": 5, "interval": 1000, "weight": 0.2468}}))
    code, out, _ = run("repo", "add", "checkpoint", populated, *files, "--desc", str(desc),
                       "--simpoint-info", str(info))
    assert code == 0
    assert out.splitlines() == [f"{populated}:e.5.0.25", f"{populated}:e.7.0.35"]

    view = json.loads(run("show", "checkpoint", "e.5.0.25", "--json")[1])
    assert (view["simpoint_index"], view["simpoint_interval"], view["simpoint_weight"], view["requested_count"]) == \
        (5, 1000, 0.2468, 3000)


def test_list_json_is_show_json(run, populated):
    for list_kind, show_kind, key in (("workloads", "workload", "name"), ("sysroots", "sysroot", "name"),
                                      ("entries", "entry", "name"), ("traces", "trace", "uuid"),
                                      ("checkpoints", "checkpoint", "name")):
        listed = json.loads(run("list", list_kind, "--json")[1])
        assert listed, list_kind
        for view in listed:
            assert view == json.loads(run("show", show_kind, view[key], "--json")[1])
