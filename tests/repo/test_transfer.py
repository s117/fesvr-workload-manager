import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from fwm.database.catalog_store import load_catalog
from fwm.database.schema import (
    CheckpointHart, DecoupledCheckpoint, DecoupledTrace, NativeEntry, Sysroot, TraceHart, TreeCatalog, Workload
)
from fwm.database.db import database_proxy
from fwm.repo import transfer as transfer_module
from fwm.repo.checkpoint import verify_checkpoint
from fwm.repo.entry import add_entry
from fwm.repo.errors import AlreadyExistsError, FwmValidationError, NotFoundError, RepoUnusableError
from fwm.repo.gc import find_leftovers
from fwm.repo.repo import Repo
from fwm.repo.subrepo import create_subrepo
from fwm.repo.sysroot import add_sysroot, verify_sysroot
from fwm.repo.trace import add_trace, verify_trace
from fwm.repo.transfer import pull, push
from fwm.utils.fs import set_tree_readonly, set_tree_writable
from tests.conftest import import_checkpoints
from tests.fesvr_files import (
    final_state_json, write_recording, write_checkpoint, Command, exit_command, recording_sha256,
    CheckpointHart as CkptHart, encode_hart_state, default_hart_state_entries
)


def commands(n):
    """The commands of a recording of its own."""
    return [Command(payload=0x1000 + n), Command(), exit_command(0)]


def record(repo, tmp_path, entry, n, instret=5000):
    rec = tmp_path / f"rec{n}"
    if not rec.exists():
        write_recording(str(rec), harts=[commands(n)])
    final_state = tmp_path / f"final-state{n}-{instret}.json"
    final_state.write_text(final_state_json(instret))
    return add_trace(repo, entry, str(rec), final_state=str(final_state)).uuid


def make_checkpoint(tmp_path, n, name, count):
    path = tmp_path / f"ckpt-{n}-{count}" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    state = encode_hart_state(default_hart_state_entries(count=count))
    write_checkpoint(str(path), [CkptHart(recording_sha256(commands(n)), cmd_index=1, state=state)], mem_mb=1)
    return str(path)


@pytest.fixture
def source(tmp_path, sysroot_src, write_entry_point):
    """
    A repository with:
    - sysroots sr1 and sr2, and sr3 that has no entry point;
    - entry points a and b of workload w1 in sr1, and c of workload w2 in sr2;
    - one trace each: a's with checkpoints a.1 (a SimPoint, validated) and a.2, b's with b.1, c's with none.
    """
    repo = Repo.init(str(tmp_path / "source"))
    other_src = tmp_path / "src" / "sr2"
    shutil.copytree(sysroot_src, other_src, symlinks=True)
    (other_src / "app" / "extra").write_text("extra")
    add_sysroot(repo, str(sysroot_src), "sr1", description="the first")
    add_sysroot(repo, str(other_src), "sr2")
    add_sysroot(repo, str(other_src), "sr3")
    add_entry(repo, "sr1", "w1", "a", write_entry_point(metadata={"suite": "s"}), description="entry a")
    add_entry(repo, "sr1", "w1", "b", write_entry_point(command="prog.rv64 other"))
    add_entry(repo, "sr2", "w2", "c", write_entry_point(copy_spawn=False))
    traces = {"a": record(repo, tmp_path, "a", 1), "b": record(repo, tmp_path, "b", 2),
              "c": record(repo, tmp_path, "c", 3)}
    import_checkpoints(repo, str(traces["a"]), [make_checkpoint(tmp_path, 1, "a.1", 1000),
                                                 make_checkpoint(tmp_path, 1, "a.2", 2000)],
                       simpoint_info={"interval": 1000, "maxK": 4, "checkpoints": {"a.1": {"index": 1, "weight": 0.5},
                                                                                   "a.2": {"index": 2, "weight": 0.5}}})
    import_checkpoints(repo, str(traces["b"]), [make_checkpoint(tmp_path, 2, "b.1", 1000)])
    DecoupledCheckpoint.update(validation_status="passed", validation_time=datetime.now().astimezone(),
                               validation_detail="ok").where(DecoupledCheckpoint.name == "a.1").execute()
    yield SimpleNamespace(repo=repo, path=repo.path, traces=traces)
    repo.close()


@pytest.fixture
def dest(tmp_path):
    repo = Repo.init(str(tmp_path / "dest"), bind=False)
    yield repo
    repo.close()


@pytest.fixture(params=["pull", "push"])
def copy(request, source, dest):
    """Copy from source to dest, by pull (dest is --repo-path) or push (source is)."""
    def run(*entries, **kwargs):
        if request.param == "pull":
            with dest.bound():
                return pull(dest, source.path, entries, **kwargs)
        with source.repo.bound():
            return push(source.repo, dest.path, entries, **kwargs)

    return run


def own(row, *references):
    return {k: v for k, v in row.__data__.items() if k != "id" and k not in references}


def snapshot(repo, entries=None, traces=True, checkpoints=True):
    """The content of a repository's database, by name, or only what copying the given entry points brings."""
    with repo.bound():
        chosen = [e for e in NativeEntry.select() if entries is None or e.name in entries]
        sysroots = {e.sysroot for e in chosen} if entries is not None else list(Sysroot.select())
        chosen_traces = [t for e in chosen for t in e.traces] if traces else []
        return {
            "workloads": {e.workload.name: own(e.workload) for e in chosen},
            "sysroots": {s.name: (own(s, "file_catalog"), load_catalog(s.file_catalog)) for s in sysroots},
            "entries": {e.name: (own(e, "workload", "sysroot"), e.workload.name, e.sysroot.name) for e in chosen},
            "traces": {t.uuid: (own(t, "native_entry", "file_catalog"), t.native_entry.name,
                                load_catalog(t.file_catalog), [own(h, "trace") for h in t.harts])
                       for t in chosen_traces},
            "checkpoints": {(c.trace.uuid, c.name): (own(c, "trace", "file_catalog"), load_catalog(c.file_catalog),
                                                     [own(h, "checkpoint") for h in c.harts])
                            for t in chosen_traces for c in (t.checkpoints if checkpoints else [])},
        }


def verify_all(repo):
    """Every problem of a repository: its items against their catalogs, and the leftovers."""
    with repo.bound():
        problems = [p for s in Sysroot.select() for p in verify_sysroot(repo, s)]
        problems += [p for t in DecoupledTrace.select() for p in verify_trace(repo, t)]
        problems += [p for c in DecoupledCheckpoint.select() for p in verify_checkpoint(repo, c)]
        return problems + [(x.path, x.reason) for x in find_leftovers(repo)]


def lines(result):
    return [str(item) for item in result.items]


# --- copying


def test_copy_one_entry(copy, source, dest):
    a = source.traces["a"]
    umask = os.umask(0o077)  # the checkpoint folder's mode is set, not left to the umask
    try:
        result = copy("a")
    finally:
        os.umask(umask)
    assert lines(result) == ["added sysroot sr1", "added entry a", f"added trace {a}",
                             f"added checkpoint {a}:a.1", f"added checkpoint {a}:a.2"]
    assert result.summary() == "added 1 sysroot, 1 entry point, 1 trace, 2 checkpoints"
    # every row as it is in the source, and nothing else
    assert snapshot(dest) == snapshot(source.repo, entries={"a"})
    assert verify_all(dest) == []
    for path in (dest.sysroot_dir("sr1"), dest.trace_dir(a), dest.checkpoint_path(a, "a.1")):
        assert os.stat(path).st_mode & 0o222 == 0
    assert os.stat(dest.checkpoint_dir(a)).st_mode & 0o777 == 0o755


def test_copy_twice(copy, source, dest):
    a = source.traces["a"]
    copy("a")
    before = snapshot(dest)
    result = copy("a")
    assert lines(result) == ["present sysroot sr1", "present entry a", f"present trace {a}",
                             f"present checkpoint {a}:a.1", f"present checkpoint {a}:a.2"]
    assert result.summary() == "present 1 sysroot, 1 entry point, 1 trace, 2 checkpoints"
    assert snapshot(dest) == before


def test_shared_sysroot(copy, source, dest):
    result = copy("a", "b", "a")
    assert [line for line in lines(result) if "sysroot" in line] == ["added sysroot sr1"]  # reported once
    assert snapshot(dest) == snapshot(source.repo, entries={"a", "b"})
    assert verify_all(dest) == []


def test_everything(copy, source, dest):
    result = copy(everything=True)
    assert lines(result)[-1] == "added sysroot sr3"
    assert snapshot(dest) == snapshot(source.repo)
    assert verify_all(dest) == []


@pytest.mark.parametrize("traces, checkpoints", [(False, True), (True, False)])
def test_leave_out(copy, source, dest, traces, checkpoints):
    copy("a", "c", traces=traces, checkpoints=checkpoints)
    assert snapshot(dest) == snapshot(source.repo, entries={"a", "c"}, traces=traces,
                                      checkpoints=traces and checkpoints)
    assert verify_all(dest) == []


def test_merge_checkpoints(copy, source, dest):
    a = source.traces["a"]
    copy("a", checkpoints=False)
    result = copy("a")
    assert lines(result)[2:] == [f"present trace {a}", f"added checkpoint {a}:a.1", f"added checkpoint {a}:a.2"]
    assert snapshot(dest) == snapshot(source.repo, entries={"a"})
    assert verify_all(dest) == []


def test_same_recording_other_uuid(copy, source, dest, tmp_path, sysroot_src, write_entry_point):
    """A trace of the same recording, imported on its own, is kept: the checkpoints attach to it."""
    with dest.bound():
        add_sysroot(dest, str(sysroot_src), "sr1")
        add_entry(dest, "sr1", "w1", "a", write_entry_point(metadata={"suite": "s"}))
        mine = record(dest, tmp_path, "a", 1)
    theirs = source.traces["a"]
    assert mine != theirs

    result = copy("a")
    assert lines(result)[2:] == [f"present trace {mine} ({theirs})", f"added checkpoint {mine}:a.1",
                                 f"added checkpoint {mine}:a.2"]
    with dest.bound():
        assert [t.uuid for t in DecoupledTrace.select()] == [mine]
        assert sorted(c.name for c in DecoupledTrace.get().checkpoints) == ["a.1", "a.2"]
    assert verify_all(dest) == []


def test_entry_keeps_its_metadata(copy, source, dest, sysroot_src, write_entry_point, caplog):
    with dest.bound():
        add_sysroot(dest, str(sysroot_src), "sr1")
        add_entry(dest, "sr1", "w1", "a", write_entry_point(metadata={"suite": "mine"}), description="mine")
    result = copy("a")
    assert lines(result)[:2] == ["present sysroot sr1", "present entry a"]
    assert "entry point 'a' keeps its metadata" in caplog.text
    with dest.bound():
        entry = NativeEntry.get()
        assert (json.loads(entry.metadata), entry.description) == ({"suite": "mine"}, "mine")


# --- conflicts


def test_conflicts(copy, source, dest, tmp_path, sysroot_src, write_entry_point):
    """Every conflict is reported, and then nothing is copied, not even what doesn't conflict."""
    a = source.traces["a"]
    copy("a", checkpoints=False)
    with dest.bound():
        add_entry(dest, "sr1", "w1", "b", write_entry_point(command="prog.rv64 mine"))
        import_checkpoints(dest, str(a), [make_checkpoint(tmp_path / "mine", 1, "a.2", 1500)])
    before = snapshot(dest)

    with pytest.raises(AlreadyExistsError) as e:
        copy("a", "b", "c")
    assert str(e.value).splitlines() == [
        f"2 conflict(s) with {dest.path}, nothing was copied:",
        f"  checkpoint {a}:a.2 exists, with different content",
        "  entry point 'b' exists, and differs from this one",
    ]
    assert e.value.exit_code == 5
    assert snapshot(dest) == before
    assert verify_all(dest) == []


def test_sysroot_conflict(copy, source, dest, tmp_path, sysroot_src):
    other = tmp_path / "other"
    shutil.copytree(sysroot_src, other, symlinks=True)
    (other / "app" / "input.txt").write_text("changed\n")
    with dest.bound():
        add_sysroot(dest, str(other), "sr1")
    with pytest.raises(AlreadyExistsError, match=r"sysroot 'sr1' exists, with different content: 1 path\(s\) differ, "
                                                 r"e.g. app/input.txt"):
        copy("a")


def test_shared_sysroot_conflicts_once(copy, source, dest, tmp_path, sysroot_src):
    """A conflicting sysroot of several entry points is one conflict."""
    other = tmp_path / "other"
    shutil.copytree(sysroot_src, other, symlinks=True)
    (other / "app" / "input.txt").write_text("changed\n")
    with dest.bound():
        add_sysroot(dest, str(other), "sr1")
    with pytest.raises(AlreadyExistsError, match=r"^1 conflict\(s\)"):
        copy("a", "b")


def test_trace_conflicts(copy, source, dest, tmp_path, sysroot_src, write_entry_point):
    with dest.bound():
        add_sysroot(dest, str(sysroot_src), "sr1")
        add_entry(dest, "sr1", "w1", "a", write_entry_point(metadata={"suite": "s"}))
        mine = record(dest, tmp_path, "a", 1, instret=6000)
    with pytest.raises(AlreadyExistsError, match=f"its recording already is trace {mine} of entry point 'a', with a "
                                                 r"different final state: instret \[6000\], not \[5000\]"):
        copy("a")

    # the UUID of another trace
    b = source.traces["b"]
    with dest.bound():
        DecoupledTrace.update(uuid=b).where(DecoupledTrace.uuid == mine).execute()
    with pytest.raises(AlreadyExistsError, match=f"trace {b} exists, of entry point 'a', with another recording"):
        copy("b")


# --- failures


def test_interrupted(copy, source, dest, monkeypatch):
    """A failure keeps the entry points already copied, removes the rest, and a rerun completes the copy."""
    real_copy = transfer_module.copy_source

    def copy_source(src, staged):
        if src.endswith("sr2"):
            raise KeyboardInterrupt
        real_copy(src, staged)

    monkeypatch.setattr(transfer_module, "copy_source", copy_source)
    with pytest.raises(KeyboardInterrupt):
        copy("a", "c")
    assert snapshot(dest) == snapshot(source.repo, entries={"a"})
    assert verify_all(dest) == []
    assert os.listdir(os.path.join(dest.path, ".staging")) == []

    monkeypatch.setattr(transfer_module, "copy_source", real_copy)
    assert [line.split()[0] for line in lines(copy("a", "c"))] == ["present"] * 5 + ["added"] * 3
    assert snapshot(dest) == snapshot(source.repo, entries={"a", "c"})


def test_failed_commit(copy, source, dest, monkeypatch):
    """A commit that fails leaves nothing, not even the checkpoint folder of the new trace."""
    real_save_catalog = transfer_module.save_catalog
    saved = []

    def save_catalog(catalog):
        saved.append(catalog)
        if len(saved) == 4:  # sysroot, trace, a.1, then a.2
            raise KeyboardInterrupt
        return real_save_catalog(catalog)

    monkeypatch.setattr(transfer_module, "save_catalog", save_catalog)
    with pytest.raises(KeyboardInterrupt):
        copy("a")
    assert snapshot(dest)["sysroots"] == {}
    assert verify_all(dest) == []
    assert os.listdir(os.path.join(dest.path, "checkpoints")) == []


def test_damaged_source(copy, source, dest):
    trace_dir = source.repo.trace_dir(source.traces["c"])
    os.chmod(os.path.join(trace_dir, "boot.elf"), 0o644)
    with open(os.path.join(trace_dir, "boot.elf"), "ab") as f:
        f.write(b"damage")
    with pytest.raises(FwmValidationError, match=r"the copy of trace .* doesn't match its catalog:\n  boot.elf: "):
        copy("a", "c")
    assert snapshot(dest) == snapshot(source.repo, entries={"a"})
    assert verify_all(dest) == []


def test_unlisted_file_in_source(copy, source, dest):
    """A file that no catalog lists, added to the source's sysroot, isn't copied with it."""
    sysroot_dir = source.repo.sysroot_dir("sr1")
    os.chmod(sysroot_dir, 0o755)
    with open(os.path.join(sysroot_dir, "unlisted"), "w") as f:
        f.write("x")
    with pytest.raises(FwmValidationError, match="doesn't match its catalog:\n  unlisted: not in the catalog"):
        copy("a")
    assert snapshot(dest)["sysroots"] == {}


def run_fwm(*args):
    """Run fwm in a process of its own, as another command would."""
    subprocess.run([sys.executable, "-m", "fwm", *args], check=True, capture_output=True)


def test_sysroot_replaced_while_read(copy, source, dest, monkeypatch):
    """Entry points of one sysroot that saw it with different content: nothing is copied."""
    real_read_entry = transfer_module._read_entry

    def read_entry(source_repo, name, *args):
        if name == "b":  # sr1 was replaced after a was read
            with sqlite3.connect(os.path.join(source.path, "fwm.db")) as db:
                db.execute("UPDATE catalogfile SET digest = ? WHERE path = 'app/input.txt'", ("0" * 64,))
        return real_read_entry(source_repo, name, *args)

    monkeypatch.setattr(transfer_module, "_read_entry", read_entry)
    with pytest.raises(FwmValidationError, match="sysroot 'sr1' of .* changed while it was read: copy again"):
        copy("a", "b")
    assert snapshot(dest)["sysroots"] == {}


def test_everything_while_entry_removed(copy, source, dest, monkeypatch):
    """With --all, a sysroot whose entry points were removed while the source was read is copied on its own."""
    real_read_entry = transfer_module._read_entry

    def read_entry(source_repo, name, *args):
        if name == "c":
            run_fwm("--repo-path", source.path, "repo", "remove", "entry", "c", "-r")
        return real_read_entry(source_repo, name, *args)

    monkeypatch.setattr(transfer_module, "_read_entry", read_entry)
    copy(everything=True)
    assert sorted(snapshot(dest)["sysroots"]) == ["sr1", "sr2", "sr3"]
    assert sorted(snapshot(dest)["entries"]) == ["a", "b"]


@pytest.mark.parametrize("table, column, where, value, match", [
    ("sysroot", "name", "name = 'sr1'", "../out", "invalid sysroot name '../out'"),
    ("nativeentry", "name", "name = 'a'", "a/b", "invalid entry point name 'a/b'"),
    ("decoupledcheckpoint", "name", "name = 'a.1'", "../../out", "invalid checkpoint name '../../out'"),
    ("catalogfile", "path", "path = 'boot.elf'", "../../out", "trace .* has an invalid path in its catalog"),
    ("catalogdir", "path", "path = 'app'", "/abs", "sysroot 'sr1' .* has an invalid path in its catalog"),
])
def test_invalid_source(copy, source, dest, tmp_path, table, column, where, value, match):
    """Names and catalog paths read from the source become paths: an invalid one (a database changed by hand) is
    refused before anything is copied."""
    with sqlite3.connect(os.path.join(source.path, "fwm.db")) as db:
        assert db.execute(f"UPDATE {table} SET {column} = ? WHERE {where}", (value,)).rowcount > 0
    around = sorted(os.listdir(tmp_path))
    with pytest.raises(FwmValidationError, match=match):
        copy(everything=True)
    assert snapshot(dest)["sysroots"] == {}
    assert sorted(os.listdir(tmp_path)) == around  # nothing written outside the repositories
    assert os.listdir(os.path.join(dest.path, ".staging")) == []


def test_present_sysroot_compared_once(copy, source, dest, monkeypatch):
    """A sysroot the destination has is compared once, however many entry points copied use it."""
    copy("a", "b")
    real_load_catalog = transfer_module.load_catalog
    destination_loads = []

    def load_catalog(tree):
        if TreeCatalog._meta.database is database_proxy:  # (the source is read through its bind_ctx())
            destination_loads.append(tree)
        return real_load_catalog(tree)

    monkeypatch.setattr(transfer_module, "load_catalog", load_catalog)
    copy("a", "b")
    assert len(destination_loads) == 1


def while_staging(monkeypatch, what, *commands):
    """Run fwm commands (each a list of arguments) when the copy first stages something whose name starts with what."""
    real_stage = transfer_module._stage
    ran = []

    def stage(dest_repo, stack, source_path, catalog, name, *args, **kwargs):
        if not ran and name.startswith(what):
            ran.append(name)
            for command in commands:
                run_fwm(*command)
        return real_stage(dest_repo, stack, source_path, catalog, name, *args, **kwargs)

    monkeypatch.setattr(transfer_module, "_stage", stage)
    return ran


def test_dest_replaces_sysroot_while_copying(copy, source, dest, tmp_path, sysroot_src, monkeypatch):
    """The destination's sysroot, the same as the source's when the copy began, replaced by another of the same name
    (whose catalog may get the same id): a conflict, found with the write lock held."""
    with dest.bound():
        add_sysroot(dest, str(sysroot_src), "sr1")
    while_staging(monkeypatch, "trace", ["--repo-path", dest.path, "repo", "remove", "sysroot", "sr1"],
                  ["--repo-path", dest.path, "repo", "add", "sysroot", str(tmp_path / "src" / "sr2"), "sr1"])
    with pytest.raises(AlreadyExistsError, match="sysroot 'sr1' exists, with different content"):
        copy("a")
    assert snapshot(dest)["entries"] == {}
    assert verify_all(dest) == []
    assert os.listdir(os.path.join(dest.path, ".staging")) == []


def test_dest_adds_same_sysroot_while_copying(copy, source, dest, sysroot_src, monkeypatch):
    """What another command added meanwhile is present: the copy staged of it is dropped."""
    while_staging(monkeypatch, "trace", ["--repo-path", dest.path, "repo", "add", "sysroot", str(sysroot_src), "sr1"])
    assert lines(copy("a"))[:2] == ["present sysroot sr1", "added entry a"]
    assert verify_all(dest) == []
    assert os.listdir(os.path.join(dest.path, ".staging")) == []


def test_dest_adds_conflict_while_copying(copy, source, dest, sysroot_src, write_entry_point, monkeypatch):
    while_staging(monkeypatch, "trace", ["--repo-path", dest.path, "repo", "add", "sysroot", str(sysroot_src), "sr1"],
                  ["--repo-path", dest.path, "repo", "add", "entry", "sr1", "w1", "a",
                   write_entry_point(command="prog.rv64 mine")])
    with pytest.raises(AlreadyExistsError, match="entry point 'a' exists, and differs from this one"):
        copy("a")
    assert [t for t in snapshot(dest)["traces"]] == []
    assert verify_all(dest) == []
    assert os.listdir(os.path.join(dest.path, ".staging")) == []


def test_dest_removes_present_sysroot_while_copying(copy, source, dest, sysroot_src, monkeypatch):
    """What was present and was removed meanwhile wasn't staged: the copy stops, and copying again completes it."""
    with dest.bound():
        add_sysroot(dest, str(sysroot_src), "sr1")
    while_staging(monkeypatch, "trace", ["--repo-path", dest.path, "repo", "remove", "sysroot", "sr1"])
    with pytest.raises(FwmValidationError, match="changed while entry point 'a' was copied: run it again"):
        copy("a")
    assert verify_all(dest) == []
    monkeypatch.undo()
    copy("a")
    assert snapshot(dest) == snapshot(source.repo, entries={"a"})


def test_source_removes_while_copying(copy, source, dest, monkeypatch):
    """Something removed from the source after it was read is not found, as when it is removed before."""
    while_staging(monkeypatch, "trace", ["--repo-path", source.path, "repo", "remove", "entry", "a", "-r"])
    with pytest.raises(NotFoundError, match=r"trace .* was removed from the source while it was copied"):
        copy("a")
    assert snapshot(dest)["entries"] == {}
    assert verify_all(dest) == []


def test_read_only_source(source, dest):
    before = snapshot(source.repo)
    source.repo.close()
    set_tree_readonly(source.path)
    with dest.bound():
        pull(dest, source.path, ["a"])
    assert snapshot(dest) == snapshot(source.repo, entries={"a"})
    assert snapshot(source.repo) == before


def test_reading_takes_no_write_lock(source, dest, monkeypatch):
    """Reading the source doesn't hold up its writers, even when it is opened for writing."""
    real_load_catalog = transfer_module.load_catalog
    begun = []

    def load_catalog(tree):
        if not begun:
            other = sqlite3.connect(os.path.join(source.path, "fwm.db"), timeout=0)
            other.execute("BEGIN IMMEDIATE")  # fails if the reading holds the write lock
            other.rollback()
            other.close()
            begun.append(True)
        return real_load_catalog(tree)

    monkeypatch.setattr(transfer_module, "load_catalog", load_catalog)
    with source.repo.bound():
        push(source.repo, dest.path, ["a"])
    assert begun


def test_reading_lets_writers_commit(source, dest, monkeypatch):
    """Each entry point is read in a transaction of its own: between them, a writer of the source can commit."""
    real_read_entry = transfer_module._read_entry
    committed = []

    def read_entry(*args, **kwargs):
        unit = real_read_entry(*args, **kwargs)
        other = sqlite3.connect(os.path.join(source.path, "fwm.db"), timeout=0)
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE NativeEntry SET description = description")
        other.commit()  # fails if a read transaction is still open
        other.close()
        committed.append(unit.entry.name)
        return unit

    monkeypatch.setattr(transfer_module, "_read_entry", read_entry)
    with source.repo.bound():
        push(source.repo, dest.path, ["a", "c"])
    assert committed == ["a", "c"]
    assert snapshot(dest) == snapshot(source.repo, entries={"a", "c"})


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anything")
def test_read_only_dest(source, dest):
    set_tree_readonly(dest.path)
    with source.repo.bound():
        with pytest.raises(RepoUnusableError, match="read-only"):
            push(source.repo, dest.path, ["a"])
    set_tree_writable(dest.path)
    assert snapshot(dest)["entries"] == {}


def test_binding_restored(source, dest):
    with source.repo.bound():
        push(source.repo, dest.path, ["c"])
        with pytest.raises(NotFoundError):
            push(source.repo, dest.path, ["nope"])
        push(source.repo, dest.path, ["a"])
        DecoupledCheckpoint.update(content_sha256="0" * 64).where(DecoupledCheckpoint.name == "a.1").execute()
        with pytest.raises(AlreadyExistsError):
            push(source.repo, dest.path, ["a"])
        assert sorted(e.name for e in NativeEntry.select()) == ["a", "b", "c"]
        assert Workload.select().count() == 2
        assert TraceHart.select().count() == 3
        assert CheckpointHart.select().count() == 3


def test_bad_input(copy, source, dest, tmp_path):
    with pytest.raises(NotFoundError, match="no entry point named 'x', 'y' in"):
        copy("a", "x", "y")
    assert snapshot(dest)["entries"] == {}
    with dest.bound():
        with pytest.raises(RepoUnusableError, match="into itself"):
            pull(dest, dest.path, ["a"])
        with pytest.raises(RepoUnusableError, match="is not an fwm repository"):
            pull(dest, str(tmp_path), ["a"])
    with source.repo.bound():
        with pytest.raises(RepoUnusableError, match="into itself"):
            push(source.repo, source.path, ["a"])


# --- subrepo


def test_subrepo(source, tmp_path):
    path = str(tmp_path / "sub")
    with source.repo.bound():
        result = create_subrepo(source.repo, path, ["a", "c"], checkpoints=False)
        assert result.summary() == "added 2 sysroots, 2 entry points, 2 traces"
        assert sorted(e.name for e in NativeEntry.select()) == ["a", "b", "c"]  # still bound to the source
    sub = Repo.open(path, bind=False)
    assert snapshot(sub) == snapshot(source.repo, entries={"a", "c"}, checkpoints=False)
    assert verify_all(sub) == []
    sub.close()


@pytest.mark.parametrize("existed", [False, True])
def test_subrepo_failed(source, tmp_path, existed):
    """A failed subrepo is removed, with the directories made for it; a directory it was created in stays as it was."""
    path = tmp_path / "x" / "y" / "sub"
    if existed:
        path.mkdir(parents=True)
        path.chmod(0o2770)
        before = path.stat()
    with source.repo.bound():
        with pytest.raises(NotFoundError):
            create_subrepo(source.repo, str(path), ["a", "nope"])
    if existed:
        assert os.listdir(path) == []
        assert (path.stat().st_mode, path.stat().st_ino) == (before.st_mode, before.st_ino)
    else:
        assert os.listdir(tmp_path).count("x") == 0


def test_subrepo_failed_keeps_others(source, tmp_path, monkeypatch):
    """A failed subrepo removes only its own: what was put meanwhile in a directory it created stays."""
    from fwm.repo import subrepo as subrepo_module
    real_push = subrepo_module.push

    def push(*args, **kwargs):
        (tmp_path / "out" / "b").mkdir()  # e.g. another subrepo, next to this one
        return real_push(*args, **kwargs)

    monkeypatch.setattr(subrepo_module, "push", push)
    with source.repo.bound():
        with pytest.raises(NotFoundError):
            create_subrepo(source.repo, str(tmp_path / "out" / "a"), ["nope"])
    assert os.listdir(tmp_path / "out") == ["b"]


def test_subrepo_failed_cleanup(source, tmp_path, monkeypatch, caplog):
    """When the new repository can't be removed, the error of the copy is the one reported."""
    from fwm.repo import subrepo as subrepo_module

    def remove_tree(path):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(subrepo_module, "remove_tree", remove_tree)
    with source.repo.bound():
        with pytest.raises(NotFoundError):
            create_subrepo(source.repo, str(tmp_path / "sub"), ["a", "nope"])
    assert "cannot remove the new repository" in caplog.text


def test_subrepo_through_link(source, tmp_path):
    """NEW_REPO may be a link to an empty directory: the repository is created there, and removed from there."""
    (tmp_path / "target").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "target")
    with source.repo.bound():
        with pytest.raises(NotFoundError):
            create_subrepo(source.repo, str(tmp_path / "link"), ["a", "nope"])
        assert os.listdir(tmp_path / "target") == []
        create_subrepo(source.repo, str(tmp_path / "link"), ["a"])
    assert (tmp_path / "link").is_symlink()
    assert "fwm.db" in os.listdir(tmp_path / "target")


def test_subrepo_refused(source, tmp_path):
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "file").write_text("x")
    with source.repo.bound():
        with pytest.raises(FwmValidationError, match="not an empty directory"):
            create_subrepo(source.repo, str(tmp_path / "full"), ["a"])
        with pytest.raises(FwmValidationError, match="inside the repository"):
            create_subrepo(source.repo, os.path.join(source.path, "sub"), ["a"])
    assert os.listdir(tmp_path / "full") == ["file"]
    assert not os.path.exists(os.path.join(source.path, "sub"))
