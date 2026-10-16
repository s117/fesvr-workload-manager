import os
import sqlite3

import pytest
from click.shell_completion import ShellComplete
from click.testing import CliRunner

from fwm.cli.completion import _natural_key
from fwm.cli.fwm import cli
from tests.fesvr_files import write_recording, write_checkpoint, CheckpointHart, Command, exit_command, recording_sha256


@pytest.fixture
def complete(tmp_path, monkeypatch):
    """Complete like the shell does; return [(value, help)]. The repository is passed with --repo-path by default."""
    monkeypatch.delenv("FWM_REPO_PATH", raising=False)
    repo_path = str(tmp_path / "repo")

    def run(*args, incomplete="", repo=True):
        words = (["--repo-path", repo_path] if repo else []) + list(args)
        items = ShellComplete(cli, {}, "fwm", "_FWM_COMPLETE").get_completions(words, incomplete)
        return [(item.value, item.help) for item in items]

    return run


def values(items):
    return [value for value, _ in items]


def naturally_sorted(items):
    return sorted(items, key=lambda item: _natural_key(item[0]))


def other_commands(name):
    """The commands of a recording of its own: the same recording can't be imported twice."""
    return [Command(payload=0x2000 + sum(map(ord, name))), exit_command(0)]


def add_trace(run, tmp_path, entry, name):
    rec = tmp_path / f"rec-{name}"
    write_recording(str(rec), harts=[other_commands(name)])
    code, out, _ = run("repo", "add", "trace", entry, str(rec))
    assert code == 0
    return out.strip()


def add_checkpoint(run, tmp_path, trace, name, commands):
    file = tmp_path / name
    write_checkpoint(str(file), [CheckpointHart(recording_sha256(commands), cmd_index=1)])
    assert run("repo", "add", "checkpoint", trace, str(file))[0] == 0


def test_names(complete, run, populated, write_entry_point):
    trace_uuid = populated
    assert run("repo", "add", "entry", "sr", "other-wl", "e2", write_entry_point())[0] == 0

    assert complete("spawn") == [("sr", "2 entry points")]
    assert complete("list", "--sysroot") == [("sr", "2 entry points")]
    assert complete("repo", "add", "entry") == [("sr", "2 entry points")]
    assert complete("repo", "add", "entry", "sr") == [("other-wl", "1 entry point"), ("wl", "1 entry point")]
    assert complete("list", "--workload", incomplete="w") == [("wl", "1 entry point")]
    assert complete("mkgen", "record") == [("e", "wl @ sr"), ("e2", "other-wl @ sr")]
    assert complete("repo", "add", "trace", incomplete="e2") == [("e2", "other-wl @ sr")]
    # (the order of a random UUID and a name depends on the UUID)
    assert complete("mkgen", "replay") == naturally_sorted([
        (trace_uuid, "e, complete, 1 checkpoint"),
        ("e", f"its trace {trace_uuid}"),
    ])
    assert values(complete("repo", "add", "checkpoint", incomplete=trace_uuid[:4])) == [trace_uuid]
    assert complete("mkgen", "load") == [("e.2.0.40", "e, weight 0.4")]
    assert values(complete("repo", "record-validation")) == ["e.2.0.40"]
    assert values(complete("repo", "set-metadata")) == ["e", "e2"]

    # new names aren't completed
    assert complete("repo", "add", "entry", "sr", "wl") == []
    assert complete("repo", "add", "sysroot", str(populated)) == []


def test_by_kind(complete, populated):
    trace_uuid = populated
    assert values(complete("show", "workload")) == ["wl"]
    assert values(complete("show", "sysroot")) == ["sr"]
    assert values(complete("show", "entry")) == ["e"]
    assert values(complete("show", "trace")) == [v for v, _ in naturally_sorted([(trace_uuid, None), ("e", None)])]
    assert values(complete("show", "checkpoint")) == ["e.2.0.40"]
    assert values(complete("repo", "remove", "entry")) == ["e"]
    assert values(complete("repo", "verify", "checkpoint")) == ["e.2.0.40"]
    # the kind is a choice
    assert values(complete("show", incomplete="s")) == ["sysroot"]


def test_list_filter(complete, populated):
    assert values(complete("list")) == ["workloads", "sysroots", "entries", "traces", "checkpoints"]
    assert values(complete("list", "entries")) == ["e"]
    assert values(complete("list", "workloads")) == ["wl"]
    assert values(complete("list", "sysroots")) == ["sr"]
    assert values(complete("list", "traces")) == ["e"]  # the filter matches the entry point
    assert values(complete("list", "checkpoints")) == ["e.2.0.40"]


def test_trace_entry_name_only_while_unique(complete, run, populated, tmp_path):
    second = add_trace(run, tmp_path, "e", "second")
    assert sorted(values(complete("mkgen", "replay"))) == sorted([populated, second])


def test_checkpoints(complete, run, populated, tmp_path):
    first = populated
    second = add_trace(run, tmp_path, "e", "second")
    add_checkpoint(run, tmp_path, second, "e.2.0.40", other_commands("second"))  # the first trace's has this name
    add_checkpoint(run, tmp_path, second, "e.10.0.60", other_commands("second"))

    # a name in two traces is offered with its trace
    assert values(complete("mkgen", "load")) == values(naturally_sorted(
        [(f"{first}:e.2.0.40", None), (f"{second}:e.2.0.40", None), ("e.10.0.60", None)]))

    # after a ':', the checkpoints of that trace, with the prefix as typed
    assert values(complete("mkgen", "load", incomplete=second[:8] + ":")) == [
        f"{second[:8]}:e.2.0.40", f"{second[:8]}:e.10.0.60"]
    assert values(complete("mkgen", "load", incomplete=second[:8] + ":e.1")) == [f"{second[:8]}:e.10.0.60"]
    assert complete("mkgen", "load", incomplete="nothing:") == []


def test_repo_from_env(complete, populated, tmp_path, monkeypatch):
    monkeypatch.setenv("FWM_REPO_PATH", str(tmp_path / "repo"))
    assert values(complete("mkgen", "record", repo=False)) == ["e"]


@pytest.mark.parametrize("setup", ["none", "not-a-repo", "other-schema"])
def test_no_usable_repo(complete, run, tmp_path, monkeypatch, capsys, setup):
    repo_path = tmp_path / "repo"
    if setup == "none":
        args = []
    elif setup == "not-a-repo":
        args = ["--repo-path", str(tmp_path / "missing")]
    else:
        with sqlite3.connect(repo_path / "fwm.db") as db:
            db.execute("UPDATE schemainfo SET version = 999")
        db.close()
        args = ["--repo-path", str(repo_path)]
    before = sorted(os.listdir(tmp_path))

    assert complete(*args, "mkgen", "record", repo=False) == []
    assert complete(*args, "show", "trace", repo=False) == []
    assert capsys.readouterr() == ("", "")
    assert sorted(os.listdir(tmp_path)) == before


def test_natural_sort():
    names = ["e.10.0.10", "e.2.0.40", "e.1.0.50", "b"]
    assert sorted(names, key=_natural_key) == ["b", "e.1.0.50", "e.2.0.40", "e.10.0.10"]


@pytest.mark.parametrize("shell, marker", [("zsh", "_FWM_COMPLETE=zsh_complete"),
                                           ("bash", "_FWM_COMPLETE=bash_complete"),
                                           ("fish", "_FWM_COMPLETE=fish_complete")])
def test_completion_command(shell, marker, monkeypatch):
    monkeypatch.delenv("FWM_REPO_PATH", raising=False)
    result = CliRunner().invoke(cli, ["completion", shell])
    assert result.exit_code == 0
    assert marker in result.stdout


def test_completion_command_unknown_shell():
    result = CliRunner().invoke(cli, ["completion", "tcsh"])
    assert result.exit_code == 2
