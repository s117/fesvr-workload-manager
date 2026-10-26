import json
import os
from typing import Dict, List

import click
import pytest
from click.testing import CliRunner

from fwm.cli.fwm import cli
from tests.data_loader.test_sim_version import SPIKE_VERSION
from tests.cli.conftest import COMMANDS, write_checkpoints, write_simpoint_info
from tests.fesvr_files import (
    final_state_json, provenance,
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
    run("repo", "compose", "add", "entry", "sr", "other-wl", "other", write_entry_point())
    _, out, _ = run("list", "entries", "--workload", "wl", "--json")
    assert [e["name"] for e in json.loads(out)] == ["e"]
    _, out, _ = run("list", "entries", "oth", "--json")
    assert [e["name"] for e in json.loads(out)] == ["other"]
    assert run("list", "traces", "--workload", "wl")[0] == 2


def test_metadata(run, populated, sysroot_src, write_entry_point, tmp_path):
    metadata = {"suite": "Toolchain test", "description": "A helloworld.", "language": ["C"],
                "auxiliary": {"validator": {"verify": [{"cmd": "diff a b", "stdout": "a.cmp"}]}}}
    assert run("repo", "compose", "add", "entry", "sr", "wl", "m", write_entry_point(metadata=metadata))[0] == 0
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
    assert run("repo", "compose", "set-metadata", "e", str(new))[:2] == (0, "e\n")
    view = json.loads(run("show", "entry", "e", "--json")[1])
    assert (view["metadata"], view["traces"]) == ({"suite": "GAPBS"}, [populated])
    new.write_text("null")
    assert run("repo", "compose", "set-metadata", "e", str(new))[0] == 0
    assert json.loads(run("show", "entry", "e", "--json")[1])["metadata"] is None

    for bad in ('["GAPBS"]', '{"suite": '):
        new.write_text(bad)
        code, _, err = run("repo", "compose", "set-metadata", "e", str(new))
        assert code == 4, err
    new.write_text("{}")
    assert run("repo", "compose", "set-metadata", "missing", str(new))[0] == 3


def test_exit_codes(run, populated, write_entry_point, tmp_path):
    # not found
    code, _, err = run("show", "entry", "missing")
    assert code == 3
    assert "error: no entry point named 'missing'" in err
    # already exists
    code, _, err = run("repo", "compose", "add", "entry", "sr", "wl", "e", write_entry_point(command="other"))
    assert code == 5
    assert "already exists" in err
    # ... and a destination outside the repository
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "f").write_text("x")
    assert run("spawn", "e", str(tmp_path / "taken"))[0] == 5
    assert run("repo", "init", str(tmp_path / "taken"), repo=False)[0] == 5
    # failed check
    code, _, err = run("repo", "compose", "remove", "sysroot", "sr")
    assert code == 4
    assert "--recursive" in err
    # usage error
    assert run("repo", "compose", "remove", "nothing", "x")[0] == 2


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
    assert run("repo", "compose", "remove", "checkpoint", "e.2.0.40")[0] == 0
    # empty after its last checkpoint, which is fine
    assert run("repo", "verify")[0] == 0
    assert run("repo", "compose", "remove", "trace", trace_uuid)[0] == 0
    assert run("repo", "compose", "remove", "entry", "e")[0] == 0
    assert json.loads(run("list", "workloads", "--json")[1]) == []  # gone with its last entry point
    assert run("repo", "compose", "remove", "sysroot", "sr", "--recursive")[0] == 0
    for kind in ("workloads", "sysroots", "entries", "traces", "checkpoints"):
        assert json.loads(run("list", kind, "--json")[1]) == []
    assert not (tmp_path / "repo" / "traces" / trace_uuid).exists()


def test_spawn(run, populated, tmp_path):
    dest = tmp_path / "copy"
    code, _, err = run("spawn", "e", str(dest))
    assert code == 0
    assert "(copy)" in err
    assert (dest / "app" / "prog.rv64").is_file() and not (dest / "app" / "prog.rv64").is_symlink()
    assert run("spawn", "e", str(dest))[0] == 5  # exists
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
    assert run("repo", "compose", "add", "checkpoint", "e", desc, checkpoints)[0] == 0
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
    code, out, _ = run("repo", "bootstrap", "mkgen", "record-fesvr-trace", "e", "-o", str(tmp_path / "r1"))
    assert (code, out) == (0, str(tmp_path / "r1" / "Makefile") + "\n")
    assert run_kind(tmp_path / "r1") == "record"
    desc = tmp_path / "d.txt"
    desc.write_text("c: 100\n")
    assert run("repo", "bootstrap", "mkgen", "create-checkpoints", "e", str(desc), "-o", str(tmp_path / "r2"))[0] == 0
    assert (tmp_path / "r2" / "ckpt.desc").is_file()
    assert run_kind(tmp_path / "r2") == "create-checkpoints"

    desc.write_text("c: 9000\n")
    code, _, err = run("repo", "bootstrap", "mkgen", "create-checkpoints", "e", str(desc), "-o", str(tmp_path / "r3"))
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
                                  ("repo", "bootstrap", "mkgen", "record-fesvr-trace", "e")]):
            assert run(*args, "-o", str(tmp_path / f"run{i}"))[0] == 0, args
        assert json.loads((tmp_path / "run1" / "run.json").read_text())["trace"]["uuid"] == populated
        # no journal or other file is left behind by reading
        assert sorted(os.listdir(repo_path)) == [".staging", "checkpoints", "fwm.db", "sysroots", "traces"]

        code, out, err = run("repo", "compose", "add", "entry", "sr", "wl", "e2", write_entry_point())
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
    code, out, err = run("repo", "compose", "add", "trace", "e", str(old))
    assert (code, out) == (4, "")
    assert "has no recording_provenance.json: it was made by a spike older than" in err

    # the spike's version comes from the provenance now: add trace takes no --sim-version
    version = tmp_path / "sim-version.txt"
    version.write_text(SPIKE_VERSION)
    assert run("repo", "compose", "add", "trace", "e", str(old), "--sim-version", str(version))[0] == 2


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
    add = ("repo", "compose", "add", "checkpoint", populated, str(desc), str(checkpoints), "--simpoint-info", str(info))
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
    assert run("repo", "compose", "add", "checkpoint", populated, str(checkpoints / "e.5.0.25"), "--desc", str(desc))[0] == 2


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

    # usage (checked first), not found and conflicts
    assert run("repo", "push", other)[0] == 2
    os.chmod(tmp_path / "repo", 0o555)
    code, _, err = run("repo", "pull", other)
    assert code == 2 and "give the entry points to copy, or --all" in err
    os.chmod(tmp_path / "repo", 0o755)
    assert run("repo", "pull", str(tmp_path / "missing"), "e")[0] == 2
    assert run("repo", "push", other, "e", "--all")[0] == 2
    assert run("repo", "push", str(tmp_path / "repo"), "e")[0] == 2
    assert run("repo", "push", other, "nope")[0] == 3
    third = str(tmp_path / "third")
    assert run("repo", "init", third, repo=False)[0] == 0
    (sysroot_src / "app" / "input.txt").write_text("changed\n")
    assert run("--repo-path", third, "repo", "compose", "add", "sysroot", str(sysroot_src), "sr", repo=False)[0] == 0
    code, out, err = run("--repo-path", third, "repo", "pull", repo_path, "--no-traces", "e", repo=False)
    assert (code, out) == (5, "")
    assert "sysroot 'sr' exists, with different content" in err


def test_subrepo(run, populated, tmp_path, caplog):
    sub = str(tmp_path / "sub")
    assert run("repo", "subrepo", sub, "e")[:2] == (0, sub + "\n")
    assert "added 1 sysroot, 1 entry point, 1 trace, 1 checkpoint" in caplog.text
    code, out, _ = run("--repo-path", sub, "list", "checkpoints", "--json", repo=False)
    assert (code, [c["name"] for c in json.loads(out)]) == (0, ["e.2.0.40"])
    assert run("repo", "subrepo", sub, "e")[0] == 5  # not empty
    assert run("repo", "subrepo", str(tmp_path / "sub2"), "nope")[0] == 3
    assert not os.path.exists(tmp_path / "sub2")
    assert run("repo", "subrepo", str(tmp_path / "sub2"))[0] == 2


def test_transfer_options(run, populated, tmp_path):
    """--no-traces and --no-checkpoints reach each command; push and subrepo only read this repository."""
    from fwm.utils.fs import set_tree_readonly

    repo_path = str(tmp_path / "repo")
    set_tree_readonly(repo_path)
    repos = {}
    for name in ("p1", "p2", "p3"):
        repos[name] = str(tmp_path / name)
        assert run("repo", "init", repos[name], repo=False)[0] == 0

    code, out, _ = run("repo", "push", repos["p1"], "e", "--no-checkpoints")
    assert code == 0 and "added trace" in out and "checkpoint" not in out
    code, out, _ = run("repo", "push", repos["p2"], "--all", "--no-traces")
    assert code == 0 and "added entry e" in out and "trace" not in out
    code, out, _ = run("--repo-path", repos["p3"], "repo", "pull", repo_path, "e", "--no-checkpoints", repo=False)
    assert code == 0 and "added trace" in out and "checkpoint" not in out
    sub = str(tmp_path / "sub")
    assert run("repo", "subrepo", sub, "e", "--no-checkpoints")[0] == 0
    code, out, _ = run("--repo-path", sub, "list", "checkpoints", "--json", repo=False)
    assert (code, json.loads(out)) == (0, [])
    code, out, _ = run("--repo-path", sub, "list", "traces", "--json", repo=False)
    assert (code, len(json.loads(out))) == (0, 1)

    # the choice of entry points is checked before the repository is opened
    code, _, err = run("--repo-path", str(tmp_path / "missing"), "repo", "push", repos["p1"], repo=False)
    assert code == 2 and "give the entry points to copy, or --all" in err


def help_sections(*args):
    """The sections of a command's help: {title: [command, ...]}."""
    text = CliRunner().invoke(cli, [*args, "--help"]).stdout
    sections: Dict[str, List[str]] = {}
    title = None
    for line in text.splitlines():
        if line and not line.startswith(" ") and line.endswith(":"):
            title = line[:-1]
            sections[title] = []
        elif title not in (None, "Usage", "Options") and line.startswith("  ") and not line.startswith("   "):
            sections[title].append(line.split()[0])
    sections.pop("Options", None)
    return sections


@pytest.mark.parametrize("args", [[], ["repo"], ["repo", "compose"], ["repo", "compose", "add"], ["repo", "bootstrap"],
                                  ["repo", "bootstrap", "mkgen"], ["mkgen"]])
def test_help_sections(args):
    """A group's help lists each of its commands once, in its section."""
    sections = help_sections(*args)
    group = cli
    for name in args:
        group = group.get_command(click.Context(group), name)
    listed = [name for names in sections.values() for name in names]
    assert sorted(listed) == sorted(group.list_commands(click.Context(group)))
    assert "Commands" not in sections  # every command is in a section
    if not args:
        assert sections["Daily"] == ["list", "show", "mkgen", "spawn"]
    elif args == ["repo"]:
        assert sections["The repository"] == ["init", "verify", "gc"]
        assert sections["Copying between repositories"] == ["pull", "push", "subrepo"]


def leaf_commands(group, prefix=()):
    """The commands of a group and of its sub-groups, as argument tuples, e.g. ("add", "trace")."""
    ctx = click.Context(group)
    for name in group.list_commands(ctx):
        command = group.get_command(ctx, name)
        if isinstance(command, click.Group):
            yield from leaf_commands(command, (*prefix, name))
        else:
            yield (*prefix, name)


def test_compose_needs_write_access(run, populated, sysroot_src, write_entry_point, tmp_path):
    """Every `fwm repo compose` command opens the repository for writing: a read-only one is refused (status 2)."""
    from fwm.utils.fs import set_tree_readonly

    rec = tmp_path / "rec2"
    write_recording(str(rec), harts=[COMMANDS])
    run_dir = tmp_path / "validate"
    run_dir.mkdir()
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}")
    desc, checkpoints = write_checkpoints(tmp_path / "ckpts", "e.9")
    calls = {
        ("add", "sysroot"): [str(sysroot_src), "sr2"],
        ("add", "entry"): ["sr", "wl", "e2", write_entry_point()],
        ("add", "trace"): ["e", str(rec)],
        ("add", "checkpoint"): [populated, desc, checkpoints],
        ("remove",): ["checkpoint", "e.2.0.40"],
        ("set-metadata",): ["e", str(metadata)],
        ("set-checkpoint-validation",): ["e.2.0.40", str(run_dir)],
    }
    compose = cli.get_command(click.Context(cli), "repo").get_command(click.Context(cli), "compose")
    assert sorted(leaf_commands(compose)) == sorted(calls)  # a new command must be added here
    set_tree_readonly(str(tmp_path / "repo"))
    for command, args in calls.items():
        code, out, err = run("repo", "compose", *command, *args)
        assert (code, out) == (2, ""), command
        assert "is read-only for you" in err, command


@pytest.mark.parametrize("args", [
    ["check", "recording"], ["repo", "bootstrap", "record", "e"], ["repo", "bootstrap", "create-checkpoints", "t", "d"],
    ["repo", "add", "sysroot"], ["repo", "remove", "entry", "e"],
    ["repo", "set-metadata", "e", "f"], ["repo", "record-validation", "c", "d"],
])
def test_old_command_names(run, args):
    code, _, err = run(*args)
    assert code == 2 and "No such command" in err


def test_bootstrap_only_reads(run, populated, tmp_path):
    """Every `fwm repo bootstrap` command works on a repository the user can't write to (check-fesvr-trace on none)."""
    from fwm.utils.fs import set_tree_readonly

    rec = tmp_path / "rec2"
    write_recording(str(rec), harts=[COMMANDS])
    final_state = tmp_path / "final-state2.json"
    final_state.write_text(final_state_json(5000))
    desc = tmp_path / "ckpt.desc"
    desc.write_text("c: 1000\n")
    calls = {
        ("mkgen", "record-fesvr-trace"): ["e", "-o", str(tmp_path / "r1")],
        ("mkgen", "create-checkpoints"): [populated, str(desc), "-o", str(tmp_path / "r2")],
        ("check-fesvr-trace",): [str(rec), "--final-state", str(final_state)],
    }
    bootstrap = cli.get_command(click.Context(cli), "repo").get_command(click.Context(cli), "bootstrap")
    assert sorted(leaf_commands(bootstrap)) == sorted(calls)  # a new command must be added here
    set_tree_readonly(str(tmp_path / "repo"))
    for command, args in calls.items():
        assert run("repo", "bootstrap", *command, *args)[0] == 0, command
    assert run("repo", "bootstrap", "check-fesvr-trace", str(rec), repo=False)[0] == 0


def all_commands(command, path=()):
    """Every command and group under command, as argument tuples (the root's too)."""
    yield path
    if isinstance(command, click.Group):
        for name in command.list_commands(click.Context(command)):
            yield from all_commands(command.get_command(click.Context(command), name), (*path, name))


@pytest.mark.parametrize("path", list(all_commands(cli)), ids=lambda path: " ".join(path) or "fwm")
def test_help_fits(path):
    """Every help fits an 80-column terminal, and lists no short help cut short ("...")."""
    result = CliRunner().invoke(cli, [*path, "--help"], terminal_width=80)
    assert result.exit_code == 0
    for line in result.stdout.splitlines():
        assert len(line) <= 80, line
        assert line.startswith("Usage:") or not line.endswith("..."), line  # ("[ARGS]..." in usage is fine)


def test_transfer_entries_or_all(run, populated, tmp_path):
    other = str(tmp_path / "other")
    assert run("repo", "init", other, repo=False)[0] == 0
    code, _, err = run("repo", "push", other, "e", "--all")
    assert code == 2 and "give either the entry points to copy or --all, not both" in err

