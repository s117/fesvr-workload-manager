"""
Imports racing with other commands, as when a pipeline runs several of them at once: another import of the same item
commits, or the parent is removed, between an import's checks and its transaction. The import must then fail cleanly,
or with --exist-ok return what the other one stored, and leave nothing behind.
"""
import json
import os
import subprocess
import sys

import pytest

from fwm.database.schema import DecoupledCheckpoint, DecoupledTrace, NativeEntry, Sysroot
from fwm.repo import remove as rm
from fwm.repo.checkpoint import add_checkpoints
from fwm.repo.entry import add_entry
from fwm.repo.errors import AlreadyExistsError, NotFoundError
from fwm.repo.gc import find_leftovers
from fwm.repo.repo import Repo
from fwm.repo.sysroot import add_sysroot
from fwm.repo.trace import add_trace
from tests.fesvr_files import write_recording, write_checkpoint, Command, exit_command, recording_sha256, CheckpointHart

COMMANDS = [Command(), exit_command(0)]


def just_before_commit(monkeypatch, action):
    """Run action once, when the next import has done its checks and copies, just before its transaction."""
    original = Repo.importing
    done = []

    def importing(self):
        if not done:
            done.append(True)
            action()
        return original(self)

    monkeypatch.setattr(Repo, "importing", importing)


def assert_clean(repo):
    assert find_leftovers(repo, min_age=0) == []


@pytest.fixture
def entry(repo, sysroot_src, write_entry_point):
    add_sysroot(repo, str(sysroot_src), "sr")
    return add_entry(repo, "sr", "wl", "e", write_entry_point())


@pytest.fixture
def recording(tmp_path):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    return str(rec)


@pytest.fixture
def checkpoints(tmp_path):
    """A description file and the directory of the checkpoints it created."""
    directory = tmp_path / "r3" / "checkpoints"
    directory.mkdir(parents=True)
    for name in ("a", "b"):
        write_checkpoint(str(directory / name), [CheckpointHart(recording_sha256(COMMANDS), cmd_index=1)])
    desc = tmp_path / "r3" / "ckpt.desc"
    desc.write_text("a: 1000\nb: 2000\n")
    return str(desc), str(directory)


@pytest.mark.parametrize("exist_ok", [False, True])
def test_same_sysroot_committed_meanwhile(repo, sysroot_src, monkeypatch, exist_ok):
    just_before_commit(monkeypatch, lambda: add_sysroot(repo, str(sysroot_src), "sr"))
    if exist_ok:
        assert add_sysroot(repo, str(sysroot_src), "sr", exist_ok=True).name == "sr"
    else:
        with pytest.raises(AlreadyExistsError):
            add_sysroot(repo, str(sysroot_src), "sr")
    assert Sysroot.select().count() == 1
    assert_clean(repo)


@pytest.mark.parametrize("exist_ok", [False, True])
def test_same_trace_committed_meanwhile(repo, entry, recording, monkeypatch, exist_ok):
    other = []
    just_before_commit(monkeypatch, lambda: other.append(add_trace(repo, "e", recording)))
    if exist_ok:
        assert add_trace(repo, "e", recording, exist_ok=True).uuid == other[0].uuid
    else:
        with pytest.raises(AlreadyExistsError):
            add_trace(repo, "e", recording)
    assert DecoupledTrace.select().count() == 1
    assert_clean(repo)


@pytest.mark.parametrize("exist_ok", [False, True])
def test_same_checkpoints_committed_meanwhile(repo, entry, recording, checkpoints, monkeypatch, exist_ok):
    trace = add_trace(repo, "e", recording)
    desc, directory = checkpoints
    just_before_commit(monkeypatch, lambda: add_checkpoints(repo, "e", desc, directory))
    if exist_ok:
        assert [c.name for c in add_checkpoints(repo, "e", desc, directory, exist_ok=True)] == ["a", "b"]
    else:
        with pytest.raises(AlreadyExistsError):
            add_checkpoints(repo, "e", desc, directory)
    assert DecoupledCheckpoint.select().count() == 2
    assert sorted(os.listdir(repo.checkpoint_dir(trace.uuid))) == ["a", "b"]
    assert_clean(repo)


def test_entry_removed_during_trace_import(repo, entry, recording, monkeypatch):
    just_before_commit(monkeypatch, lambda: rm.remove_entry(repo, "e"))
    with pytest.raises(NotFoundError, match="was removed during the import"):
        add_trace(repo, "e", recording)
    assert DecoupledTrace.select().count() == 0
    assert_clean(repo)


def test_trace_removed_during_checkpoint_import(repo, entry, recording, checkpoints, monkeypatch):
    trace = add_trace(repo, "e", recording)
    just_before_commit(monkeypatch, lambda: rm.remove_trace(repo, str(trace.uuid)))
    with pytest.raises(NotFoundError, match="was removed during the import"):
        add_checkpoints(repo, str(trace.uuid), *checkpoints)
    assert DecoupledCheckpoint.select().count() == 0
    assert not os.path.exists(repo.checkpoint_dir(trace.uuid))
    assert_clean(repo)


def test_sysroot_removed_while_adding_entry(repo, sysroot_src, write_entry_point, monkeypatch):
    add_sysroot(repo, str(sysroot_src), "sr")
    import fwm.repo.entry as entry_module
    check_paths = entry_module._check_paths

    def check_then_remove(*args):
        check_paths(*args)
        rm.remove_sysroot(repo, "sr")

    monkeypatch.setattr(entry_module, "_check_paths", check_then_remove)
    with pytest.raises(NotFoundError, match="was removed while adding the entry point"):
        add_entry(repo, "sr", "wl", "e", write_entry_point())
    assert NativeEntry.select().count() == 0


def fwm_process(repo, *args):
    return subprocess.Popen([sys.executable, "-m", "fwm", "--repo-path", repo.path, *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_concurrent_processes(repo, entry, recording, checkpoints, sysroot_src):
    """Several fwm processes changing one repository at once wait for each other, and all succeed."""
    repo.close()  # this process doesn't hold the database meanwhile
    desc, directory = checkpoints
    procs = [fwm_process(repo, "repo", "compose", "add", "sysroot", str(sysroot_src), f"sr{i}") for i in range(4)]
    procs += [fwm_process(repo, "repo", "compose", "add", "sysroot", str(sysroot_src), "sr", "--exist-ok") for _ in range(2)]
    traces = [fwm_process(repo, "repo", "compose", "add", "trace", "e", recording, "--exist-ok") for _ in range(3)]
    results = [(p.wait(timeout=120), p.communicate()) for p in procs + traces]
    assert [code for code, _ in results] == [0] * len(results), [err for _, (_, err) in results]
    assert len({out for _, (out, _) in results[len(procs):]}) == 1  # one trace UUID, printed by all three

    ckpts = [fwm_process(repo, "repo", "compose", "add", "checkpoint", "e", desc, directory, "--exist-ok") for _ in range(3)]
    outputs = [(p.wait(timeout=120), p.communicate()) for p in ckpts]
    assert [code for code, _ in outputs] == [0] * 3, [err for _, (_, err) in outputs]
    assert len({out for _, (out, _) in outputs}) == 1

    listed = subprocess.run([sys.executable, "-m", "fwm", "--repo-path", repo.path, "list", "sysroots", "--json"],
                            check=True, capture_output=True, text=True).stdout
    assert sorted(s["name"] for s in json.loads(listed)) == ["sr", "sr0", "sr1", "sr2", "sr3"]
    verify = subprocess.run([sys.executable, "-m", "fwm", "--repo-path", repo.path, "repo", "verify"],
                            capture_output=True, text=True)
    assert verify.returncode == 0, verify.stdout
