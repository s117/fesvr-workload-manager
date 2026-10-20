import json
import os

from click.testing import CliRunner

from fwm.cli.fwm import cli
from tests.data_loader.test_sim_version import SPIKE_VERSION
from tests.cli.conftest import COMMANDS, write_checkpoints, write_simpoint_info
from tests.fesvr_files import (
    provenance,
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
    assert [(e["name"], e["workload"], e["sysroot"], e["traces"], e["num_checkpoints"]) for e in entries] == [
        ("e", "wl", "sr", [trace_uuid], 1)]

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
    # empty after its last checkpoint, which is fine
    assert run("repo", "verify")[0] == 0
    assert run("repo", "remove", "trace", trace_uuid)[0] == 0
    assert run("repo", "remove", "entry", "e")[0] == 0
    assert json.loads(run("list", "workloads", "--json")[1]) == []  # gone with its last entry point
    assert run("repo", "remove", "sysroot", "sr", "--recursive")[0] == 0
    for kind in ("workloads", "sysroots", "entries", "traces", "checkpoints"):
        assert json.loads(run("list", kind, "--json")[1]) == []
    assert not (tmp_path / "repo" / "traces" / trace_uuid).exists()


def test_spawn(run, populated, tmp_path):
    dest = tmp_path / "copy"
    code, _, err = run("spawn", "e", str(dest))
    assert code == 0
    assert "(copy)" in err
    assert (dest / "app" / "prog.rv64").is_file() and not (dest / "app" / "prog.rv64").is_symlink()
    assert run("spawn", "e", str(dest))[0] == 4
    code, _, err = run("spawn", "--force", "--mode", "link", "e", str(dest))
    assert code == 0
    assert "(link)" in err
    assert (dest / "app" / "prog.rv64").is_symlink()
    assert run("spawn", "--mode", "other", "e", str(dest))[0] == 2
    assert run("spawn", "sr", str(tmp_path / "x"))[0] == 3  # an entry point, not a sysroot


def run_kind(run_dir) -> str:
    return json.loads((run_dir / "run.json").read_text())["kind"]


def test_mkgen(run, populated, tmp_path, caplog, write_entry_point):
    code, out, _ = run("mkgen", "from-sysroot", "e", "-o", str(tmp_path / "r1"))
    assert (code, out) == (0, str(tmp_path / "r1" / "Makefile") + "\n")
    assert f"make -C {tmp_path / 'r1'} run" in caplog.text
    assert run_kind(tmp_path / "r1") == "from-sysroot"
    assert run("mkgen", "from-fesvr-trace", "e", "-o", str(tmp_path / "r2"))[0] == 0
    assert run_kind(tmp_path / "r2") == "from-fesvr-trace"

    # a SimPoint checkpoint: from-checkpoint suggests from-simpoint
    caplog.clear()
    assert run("mkgen", "from-checkpoint", "e.2.0.40", "-o", str(tmp_path / "r3"))[0] == 0
    assert "fwm mkgen from-simpoint e.2.0.40" in caplog.text
    assert run("mkgen", "from-simpoint", "e.2.0.40", "-o", str(tmp_path / "r4"))[0] == 0
    assert "INTERVAL := 1000\n" in (tmp_path / "r4" / "Makefile").read_text()

    # a checkpoint without SimPoint information
    desc, checkpoints = write_checkpoints(tmp_path / "plain", "plain")
    assert run("repo", "add", "checkpoint", "e", desc, checkpoints)[0] == 0
    caplog.clear()
    assert run("mkgen", "from-checkpoint", "plain", "-o", str(tmp_path / "r5"))[0] == 0
    assert "from-simpoint" not in caplog.text
    code, _, err = run("mkgen", "from-simpoint", "plain", "-o", str(tmp_path / "r6"))
    assert code == 4
    assert "no SimPoint information" in err

    # the former commands are gone
    for old in ("record", "replay", "load"):
        assert run("mkgen", old, "e")[0] == 2


def test_bootstrap(run, populated, tmp_path, caplog):
    code, out, _ = run("repo", "bootstrap", "record", "e", "-o", str(tmp_path / "r1"))
    assert (code, out) == (0, str(tmp_path / "r1" / "Makefile") + "\n")
    assert run_kind(tmp_path / "r1") == "record"
    desc = tmp_path / "d.txt"
    desc.write_text("c: 100\n")
    assert run("repo", "bootstrap", "create-checkpoints", "e", str(desc), "-o", str(tmp_path / "r2"))[0] == 0
    assert (tmp_path / "r2" / "ckpt.desc").is_file()
    assert run_kind(tmp_path / "r2") == "create-checkpoints"

    desc.write_text("c: 9000\n")
    code, _, err = run("repo", "bootstrap", "create-checkpoints", "e", str(desc), "-o", str(tmp_path / "r3"))
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
        for i, args in enumerate([("mkgen", "from-sysroot", "e"), ("mkgen", "from-fesvr-trace", populated),
                                  ("mkgen", "from-checkpoint", "e.2.0.40"), ("mkgen", "from-simpoint", "e.2.0.40"),
                                  ("repo", "bootstrap", "record", "e")]):
            assert run(*args, "-o", str(tmp_path / f"run{i}"))[0] == 0, args
        assert json.loads((tmp_path / "run1" / "run.json").read_text())["trace"]["uuid"] == populated
        # no journal or other file is left behind by reading
        assert sorted(os.listdir(repo_path)) == [".staging", "checkpoints", "fwm.db", "sysroots", "traces"]

        code, out, err = run("repo", "add", "entry", "sr", "wl", "e2", write_entry_point())
        assert (code, out) == (2, "")
        assert "is read-only for you" in err
    finally:
        for p in readonly:
            p.chmod(p.stat().st_mode | 0o200)


def test_provenance(run, populated, tmp_path):
    """A trace keeps how it was recorded: its recording_provenance.json."""
    view = json.loads(run("show", "trace", populated, "--json")[1])
    assert view["provenance"] == provenance()
    assert "sim_version" not in view
    out = run("show", "trace", populated)[1]
    for line in ("recorded: 2026-10-17T18:57:50-0400 - 2026-10-17T18:59:12-0400 EDT",
                 "on: recorder (Linux 6.18.49, x86_64)", "simulator: spike heads/main-0-gfd01c25 (fd01c257b2d7)",
                 "fesvr: heads/main-0-g8472993 (8472993ef664)", "command: spike -m1 +dev-traffic-record=rec",
                 "cwd: /work"):
        assert line in out
    assert "RECORDED ON" in run("list", "traces")[1] and "recorder" in run("list", "traces")[1]

    # a recording made by an older spike, without one, is refused
    old = tmp_path / "old"
    write_recording(str(old), harts=[[Command(payload=0x3000), exit_command(0)]], prov=None)
    code, out, err = run("repo", "add", "trace", "e", str(old))
    assert (code, out) == (4, "")
    assert "has no recording_provenance.json: it was made by a spike older than" in err

    # the spike's version comes from the provenance now: add trace takes no --sim-version
    version = tmp_path / "sim-version.txt"
    version.write_text(SPIKE_VERSION)
    assert run("repo", "add", "trace", "e", str(old), "--sim-version", str(version))[0] == 2


def test_add_checkpoints(run, populated, tmp_path):
    checkpoints = tmp_path / "r3" / "checkpoints"
    checkpoints.mkdir(parents=True)
    for name, count in (("e.5.0.25", 3000), ("e.7.0.35", 4000)):
        write_checkpoint(str(checkpoints / name), [CheckpointHart(
            recording_sha256(COMMANDS), cmd_index=1, state=encode_hart_state(default_hart_state_entries(count=count)))])
    (checkpoints / "bbv_proc_0.bb.gz").write_bytes(b"ignored")
    desc = tmp_path / "r3" / "ckpt.desc"
    desc.write_text("e.5.0.25: 3000\ne.7.0.35: 4000\n")
    info = write_simpoint_info(tmp_path, {"e.5.0.25": (5, 0.2468), "e.7.0.35": (7, 0.3532)})
    add = ("repo", "add", "checkpoint", populated, str(desc), str(checkpoints), "--simpoint-info", str(info))
    code, out, _ = run(*add)
    assert code == 0
    assert out.splitlines() == [f"{populated}:e.5.0.25", f"{populated}:e.7.0.35"]

    view = json.loads(run("show", "checkpoint", "e.5.0.25", "--json")[1])
    assert (view["simpoint_index"], view["simpoint_interval"], view["simpoint_max_k"], view["simpoint_weight"],
            view["requested_count"]) == (5, 1000, 5, 0.2468, 3000)

    # rerun
    assert run(*add)[0] == 5
    assert run(*add, "--exist-ok")[:2] == (0, out)
    # a checkpoint of the description that wasn't created
    desc.write_text("e.5.0.25: 3000\ne.9.0.40: 99999\n")
    code, _, err = run(*add[:-2])
    assert code == 4
    assert "e.9.0.40 (99999)" in err
    # the former interface
    assert run("repo", "add", "checkpoint", populated, str(checkpoints / "e.5.0.25"), "--desc", str(desc))[0] == 2


def test_list_json_is_show_json(run, populated):
    for list_kind, show_kind, key in (("workloads", "workload", "name"), ("sysroots", "sysroot", "name"),
                                      ("entries", "entry", "name"), ("traces", "trace", "uuid"),
                                      ("checkpoints", "checkpoint", "name")):
        listed = json.loads(run("list", list_kind, "--json")[1])
        assert listed, list_kind
        for view in listed:
            assert view == json.loads(run("show", show_kind, view[key], "--json")[1])


def test_pull_and_push(run, populated, sysroot_src, tmp_path, caplog):
    trace_uuid = populated
    repo_path = str(tmp_path / "repo")
    other = str(tmp_path / "other")
    assert run("repo", "init", other, repo=False)[0] == 0

    code, out, err = run("--repo-path", other, "repo", "pull", repo_path, "e", repo=False)
    assert (code, out) == (0, f"added sysroot sr\nadded entry e\nadded trace {trace_uuid}\n"
                              f"added checkpoint {trace_uuid}:e.2.0.40\n")
    assert "added 1 sysroot, 1 entry point, 1 trace, 1 checkpoint" in caplog.text
    code, out, err = run("repo", "push", other, "--all")
    assert (code, out) == (0, f"present sysroot sr\npresent entry e\npresent trace {trace_uuid}\n"
                              f"present checkpoint {trace_uuid}:e.2.0.40\n")
    assert run("--repo-path", other, "repo", "verify", repo=False)[0] == 0
    assert run("--repo-path", other, "mkgen", "from-checkpoint", "e.2.0.40", "-o", str(tmp_path / "r"),
               repo=False)[0] == 0

    # usage, not found and conflicts
    assert run("repo", "push", other)[0] == 2
    assert run("repo", "push", other, "e", "--all")[0] == 2
    assert run("repo", "push", str(tmp_path / "repo"), "e")[0] == 2
    assert run("repo", "push", other, "nope")[0] == 3
    third = str(tmp_path / "third")
    assert run("repo", "init", third, repo=False)[0] == 0
    (sysroot_src / "app" / "input.txt").write_text("changed\n")
    assert run("--repo-path", third, "repo", "add", "sysroot", str(sysroot_src), "sr", repo=False)[0] == 0
    code, out, err = run("--repo-path", third, "repo", "pull", repo_path, "--no-traces", "e", repo=False)
    assert (code, out) == (5, "")
    assert "sysroot 'sr' exists, with different content" in err
