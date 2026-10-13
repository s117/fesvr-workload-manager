import os
import uuid
from types import SimpleNamespace

import pytest

from fwm.database.schema import (
    Sysroot, Workload, NativeEntry, DecoupledTrace, TraceHart, DecoupledCheckpoint, CheckpointHart, TreeCatalog
)
from fwm.repo import remove as rm
from fwm.repo.checkpoint import add_checkpoint, resolve_checkpoint, decode_simpoint_weight, verify_checkpoint
from fwm.repo.entry import add_entry
from fwm.repo.errors import FwmValidationError, NotFoundError
from fwm.repo.repo import Repo
from fwm.repo.sysroot import add_sysroot, verify_sysroot, spawn_sysroot
from fwm.repo.trace import add_trace, resolve_trace, verify_trace
from tests.fesvr_files import (
    write_recording, write_checkpoint, Command, exit_command, recording_sha256, CheckpointHart as CkptHart,
    encode_hart_state, default_hart_state_entries
)
from tests.conftest import mode

COMMANDS = [Command(), Command(), exit_command(0)]


# --- repository


def test_init_and_open(tmp_path):
    path = str(tmp_path / "r")
    Repo.init(path).close()
    for d in ("sysroots", "traces", "checkpoints", ".staging", "fwm.db"):
        assert os.path.exists(os.path.join(path, d))
    Repo.open(path).close()


def test_init_non_empty(tmp_path):
    (tmp_path / "file").write_text("x")
    with pytest.raises(FwmValidationError):
        Repo.init(str(tmp_path))


def test_open_not_a_repo(tmp_path):
    with pytest.raises(NotFoundError):
        Repo.open(str(tmp_path))


# --- sysroots


def test_add_sysroot(repo, sysroot_src):
    sysroot = add_sysroot(repo, str(sysroot_src), "sr", description="test")
    assert sysroot.description == "test"
    stored = repo.sysroot_dir("sr")
    with open(os.path.join(stored, "app", "input.txt")) as f:
        assert f.read() == "1\n2\n"
    # the copy is read-only, the catalog keeps the original permissions
    assert mode(os.path.join(stored, "app", "data")) == 0o500
    assert mode(os.path.join(stored, "app", "prog.rv64")) == 0o550
    assert os.readlink(os.path.join(stored, "app", "abs-link")) == "/boot/pk"
    perms = {f.path: f.perm for f in sysroot.file_catalog.files}
    assert perms["app/prog.rv64"] == 0o750
    assert perms["app/data/private"] == 0o600
    assert verify_sysroot(repo, sysroot) == []
    assert os.listdir(os.path.join(repo.path, ".staging")) == []


def test_add_sysroot_warns_absolute_links(repo, sysroot_src, caplog):
    add_sysroot(repo, str(sysroot_src), "sr")
    assert "app/abs-link -> /boot/pk" in caplog.text


def test_add_sysroot_duplicate(repo, sysroot_src):
    add_sysroot(repo, str(sysroot_src), "sr")
    with pytest.raises(FwmValidationError, match="already exists"):
        add_sysroot(repo, str(sysroot_src), "sr")


@pytest.mark.parametrize("name", ["a/b", "..", "", "x y"])
def test_add_sysroot_bad_name(repo, sysroot_src, name):
    with pytest.raises(FwmValidationError, match="invalid sysroot name"):
        add_sysroot(repo, str(sysroot_src), name)


def test_add_sysroot_missing_dir(repo, tmp_path):
    with pytest.raises(NotFoundError):
        add_sysroot(repo, str(tmp_path / "missing"), "sr")


def test_verify_sysroot_tampered(repo, sysroot_src):
    sysroot = add_sysroot(repo, str(sysroot_src), "sr")
    path = os.path.join(repo.sysroot_dir("sr"), "app", "input.txt")
    os.chmod(path, 0o644)
    with open(path, "w") as f:
        f.write("3\n4\n")
    os.chmod(path, 0o444)
    problems = verify_sysroot(repo, sysroot)
    assert [p[0] for p in problems] == ["app/input.txt"]
    assert "SHA256 mismatch" in problems[0][1]


def test_spawn_sysroot(repo, sysroot_src, tmp_path):
    add_sysroot(repo, str(sysroot_src), "sr")
    dest = tmp_path / "run" / "sysroot"
    dest.parent.mkdir()
    spawn_sysroot(repo, "sr", str(dest))
    # the catalog's permissions are back
    assert mode(dest / "app" / "prog.rv64") == 0o750
    assert mode(dest / "app" / "data") == 0o700
    assert mode(dest / "app") == mode(sysroot_src / "app")
    (dest / "app" / "output.txt").write_text("written by the program")

    with pytest.raises(FwmValidationError, match="already exists"):
        spawn_sysroot(repo, "sr", str(dest))
    spawn_sysroot(repo, "sr", str(dest), force=True)
    assert not (dest / "app" / "output.txt").exists()


def test_spawn_missing_sysroot(repo, tmp_path):
    with pytest.raises(NotFoundError):
        spawn_sysroot(repo, "nope", str(tmp_path / "x"))


# --- entry points


def test_add_entry(repo, sysroot_src, write_entry_point):
    add_sysroot(repo, str(sysroot_src), "sr")
    entry = add_entry(repo, "sr", "prog", "prog-test", write_entry_point(stdin_file="input.txt"), description="d")
    assert entry.workload.name == "prog"
    assert entry.sysroot.name == "sr"
    assert entry.command == "prog.rv64 input.txt"
    assert entry.stdin_file == "input.txt"
    assert entry.boot_elf == "/boot/pk"
    assert entry.nprocs == 1
    assert entry.description == "d"


def test_workloads_and_sysroots_many_to_many(repo, sysroot_src, write_entry_point):
    add_sysroot(repo, str(sysroot_src), "sr1")
    add_sysroot(repo, str(sysroot_src), "sr2")
    add_entry(repo, "sr1", "bfs", "bfs-1", write_entry_point())
    add_entry(repo, "sr2", "bfs", "bfs-2", write_entry_point())
    add_entry(repo, "sr1", "pr", "pr-1", write_entry_point())
    assert sorted(e.name for e in Workload.get(Workload.name == "bfs").entries) == ["bfs-1", "bfs-2"]
    assert sorted(e.name for e in Sysroot.get(Sysroot.name == "sr1").entries) == ["bfs-1", "pr-1"]


@pytest.mark.parametrize("overrides, match", [
    ({"envs": {"A": "B"}}, "envs is not supported"),
    ({"boot_elf": "/boot/missing"}, "boot_elf /boot/missing"),
    ({"boot_elf": "pk-link"}, "is a symlink"),
    ({"boot_elf": "/app/data"}, "not a regular file"),
    ({"stdin_file": "missing.txt"}, "stdin_file missing.txt"),
    ({"working_directory": "/nope"}, "not a directory of the sysroot"),
    ({"working_directory": "/app/abs-link"}, "is a symlink"),
    ({"command": "  "}, "the command is empty"),
    ({"nprocs": 2}, "invalid entry point"),
    ({"proxy_kernel": "/boot/pk"}, "invalid entry point"),
])
def test_add_entry_invalid(repo, sysroot_src, write_entry_point, overrides, match):
    add_sysroot(repo, str(sysroot_src), "sr")
    with pytest.raises(FwmValidationError, match=match):
        add_entry(repo, "sr", "wl", "e", write_entry_point(**overrides))
    assert NativeEntry.select().count() == 0
    assert Workload.select().count() == 0


def test_add_entry_relative_paths(repo, sysroot_src, write_entry_point):
    add_sysroot(repo, str(sysroot_src), "sr")
    # relative to the working directory, which is relative to "/"
    add_entry(repo, "sr", "wl", "e", write_entry_point(working_directory="app", boot_elf="../boot/pk",
                                                       stdin_file="./input.txt"))


def test_add_entry_duplicate(repo, sysroot_src, write_entry_point):
    add_sysroot(repo, str(sysroot_src), "sr")
    add_entry(repo, "sr", "wl", "e", write_entry_point())
    with pytest.raises(FwmValidationError, match="already exists"):
        add_entry(repo, "sr", "wl", "e", write_entry_point())


def test_add_entry_missing_sysroot(repo, write_entry_point):
    with pytest.raises(NotFoundError):
        add_entry(repo, "nope", "wl", "e", write_entry_point())


# --- traces


@pytest.fixture
def entry(repo, sysroot_src, write_entry_point):
    add_sysroot(repo, str(sysroot_src), "sr")
    return add_entry(repo, "sr", "wl", "e", write_entry_point())


def write_final_state(tmp_path, *instrets) -> str:
    path = tmp_path / "final-state.json"
    path.write_text('{"hart_state": [' + ", ".join(f'{{"instret": {i}}}' for i in instrets) + "]}")
    return str(path)


def test_add_trace(repo, entry, tmp_path):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    trace = add_trace(repo, "e", str(rec), final_state=write_final_state(tmp_path, 12345), description="t")
    assert trace.native_entry.name == "e"
    assert trace.mem_mb == 1
    assert trace.num_harts == 1
    assert trace.exit_status == 0
    assert trace.description == "t"
    harts = list(trace.harts)
    assert [(h.hart_id, h.recording_sha256, h.num_commands, h.instret) for h in harts] == [
        (0, recording_sha256(COMMANDS), 3, 12345)]
    stored = repo.trace_dir(trace.uuid)
    assert sorted(os.listdir(stored)) == sorted(os.listdir(rec))
    assert mode(stored) == 0o555
    assert verify_trace(repo, trace) == []


def test_add_truncated_trace(repo, entry, tmp_path, caplog):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[[Command(), Command()]])
    trace = add_trace(repo, "e", str(rec))
    assert trace.exit_status is None
    assert [h.instret for h in trace.harts] == [None]
    assert "truncated" in caplog.text


@pytest.mark.parametrize("kwargs, match", [
    ({"mem_mb": 2}, "2 MB system"),
    ({"boot_elf": b"another pk"}, "booted an ELF of SHA256"),
    ({"harts": [COMMANDS, COMMANDS]}, "2-HART system"),
])
def test_add_trace_mismatch(repo, entry, tmp_path, kwargs, match):
    rec = tmp_path / "rec"
    write_recording(str(rec), **kwargs)
    with pytest.raises(FwmValidationError, match=match):
        add_trace(repo, "e", str(rec))
    assert DecoupledTrace.select().count() == 0
    assert os.listdir(os.path.join(repo.path, "traces")) == []


def test_add_trace_bad_recording(repo, entry, tmp_path):
    rec = tmp_path / "rec"
    write_recording(str(rec))
    os.remove(rec / "boot.elf")
    with pytest.raises(FwmValidationError, match="has no boot.elf"):
        add_trace(repo, "e", str(rec))


def test_add_trace_final_state_mismatch(repo, entry, tmp_path):
    rec = tmp_path / "rec"
    write_recording(str(rec))
    with pytest.raises(FwmValidationError, match="2 HART"):
        add_trace(repo, "e", str(rec), final_state=write_final_state(tmp_path, 1, 2))


def test_add_trace_empty_final_state(repo, entry, tmp_path):
    # spike leaves +final-state-dump empty when -e stops the run
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[[Command()]])
    final_state = tmp_path / "final-state.json"
    final_state.write_text("")
    with pytest.raises(FwmValidationError, match="is empty"):
        add_trace(repo, "e", str(rec), final_state=str(final_state))


def test_resolve_trace(repo, entry, tmp_path, monkeypatch):
    uuids = iter([uuid.UUID("12345678-0000-0000-0000-000000000001"), uuid.UUID("12345678-0000-0000-0000-000000000002")])
    monkeypatch.setattr("fwm.repo.trace.uuid", SimpleNamespace(uuid4=lambda: next(uuids)))
    rec = tmp_path / "rec"
    write_recording(str(rec))
    trace = add_trace(repo, "e", str(rec))
    assert resolve_trace("e").uuid == trace.uuid
    assert resolve_trace(str(trace.uuid)).uuid == trace.uuid
    assert resolve_trace("12345678-0000-0000-0000-00000000000").uuid == trace.uuid

    add_trace(repo, "e", str(rec))
    with pytest.raises(FwmValidationError, match="has 2 traces"):
        resolve_trace("e")
    with pytest.raises(FwmValidationError, match="matches 2 traces"):
        resolve_trace("12345678")
    assert resolve_trace("12345678000000000000000000000002").uuid.hex.endswith("2")
    for ref in ("not-a-trace", "", "9"):
        with pytest.raises(NotFoundError):
            resolve_trace(ref)


# --- checkpoints


@pytest.fixture
def trace(repo, entry, tmp_path):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    return add_trace(repo, "e", str(rec), final_state=write_final_state(tmp_path, 12345))


def make_checkpoint(tmp_path, name, rec_sha=None, cmd_index=1, count=1000, **kwargs) -> str:
    path = tmp_path / name
    state = encode_hart_state(default_hart_state_entries(count=count))
    write_checkpoint(str(path), [CkptHart(rec_sha or recording_sha256(COMMANDS), cmd_index=cmd_index, state=state)],
                     mem_mb=1, **kwargs)
    return str(path)


@pytest.mark.parametrize("name, weight", [
    ("e.3.0.25", 0.25), ("e.0.1.00", 1.0), ("astar.1G", None), ("e.1.5", None), ("e.x.0.25", None)
])
def test_decode_simpoint_weight(name, weight):
    assert decode_simpoint_weight(name) == weight


def test_add_checkpoint(repo, trace, tmp_path):
    file = make_checkpoint(tmp_path, "e.3.0.25", cmd_index=2, count=777)
    ckpt = add_checkpoint(repo, "e", file, description="c")
    assert ckpt.name == "e.3.0.25"
    assert ckpt.simpoint_weight == 0.25
    assert ckpt.format_version == 1
    assert len(ckpt.content_sha256) == 64
    assert [(h.hart_id, h.cmd_index, h.csr_count, h.hart_state_version) for h in ckpt.harts] == [(0, 2, 777, 1)]
    stored = repo.checkpoint_path(trace.uuid, "e.3.0.25")
    assert mode(stored) == 0o444
    assert verify_checkpoint(repo, ckpt) == []


def test_add_checkpoint_name_and_weight(repo, trace, tmp_path):
    file = make_checkpoint(tmp_path, "e.3.0.25")
    ckpt = add_checkpoint(repo, "e", file, name="custom", simpoint_weight=0.5)
    assert ckpt.name == "custom"
    assert ckpt.simpoint_weight == 0.5
    assert add_checkpoint(repo, "e", file, name="no-weight").simpoint_weight is None


@pytest.mark.parametrize("kwargs, match", [
    ({"name": "a/b"}, "invalid checkpoint name"),
    ({"simpoint_weight": 1.5}, r"not in \[0, 1\]"),
])
def test_add_checkpoint_bad_args(repo, trace, tmp_path, kwargs, match):
    with pytest.raises(FwmValidationError, match=match):
        add_checkpoint(repo, "e", make_checkpoint(tmp_path, "c"), **kwargs)


@pytest.mark.parametrize("ckpt_kwargs, match", [
    ({"rec_sha": "ab" * 32}, "created from the recording"),
    ({"cmd_index": 4}, "resumes at command 4"),
    ({"boot_elf": b"other"}, "boot ELF has SHA256"),
])
def test_add_checkpoint_wrong_trace(repo, trace, tmp_path, ckpt_kwargs, match):
    with pytest.raises(FwmValidationError, match=match):
        add_checkpoint(repo, "e", make_checkpoint(tmp_path, "c", **ckpt_kwargs))
    assert DecoupledCheckpoint.select().count() == 0


def test_add_checkpoint_corrupt(repo, trace, tmp_path):
    file = make_checkpoint(tmp_path, "c", trailing=b"junk")
    with pytest.raises(FwmValidationError, match="invalid checkpoint"):
        add_checkpoint(repo, "e", file)


def test_add_checkpoint_duplicate(repo, trace, tmp_path):
    file = make_checkpoint(tmp_path, "c")
    add_checkpoint(repo, "e", file)
    with pytest.raises(FwmValidationError, match="already has a checkpoint"):
        add_checkpoint(repo, "e", file)


def test_resolve_checkpoint(repo, entry, trace, tmp_path):
    file = make_checkpoint(tmp_path, "c")
    add_checkpoint(repo, "e", file)
    assert resolve_checkpoint("c").trace.uuid == trace.uuid
    assert resolve_checkpoint(f"{trace.uuid}:c").name == "c"

    # another trace with a checkpoint of the same name
    trace2 = add_trace(repo, "e", str(tmp_path / "rec"))
    add_checkpoint(repo, str(trace2.uuid), file)
    with pytest.raises(FwmValidationError, match="give it as <trace>:<name>"):
        resolve_checkpoint("c")
    assert resolve_checkpoint(f"{trace2.uuid.hex[:12]}:c").trace.uuid == trace2.uuid
    with pytest.raises(NotFoundError):
        resolve_checkpoint("missing")


# --- removal


def test_remove_checkpoint(repo, trace, tmp_path):
    add_checkpoint(repo, "e", make_checkpoint(tmp_path, "c"))
    rm.remove_checkpoint(repo, "c")
    assert DecoupledCheckpoint.select().count() == 0
    assert CheckpointHart.select().count() == 0
    assert not os.path.exists(repo.checkpoint_path(trace.uuid, "c"))


def test_remove_trace(repo, trace, tmp_path):
    add_checkpoint(repo, "e", make_checkpoint(tmp_path, "c"))
    with pytest.raises(FwmValidationError, match="has 1 checkpoint"):
        rm.remove_trace(repo, "e")
    rm.remove_trace(repo, "e", recursive=True)
    assert DecoupledTrace.select().count() == 0
    assert TraceHart.select().count() == 0
    assert DecoupledCheckpoint.select().count() == 0
    assert not os.path.exists(repo.trace_dir(trace.uuid))
    assert not os.path.exists(repo.checkpoint_dir(trace.uuid))


def test_remove_entry_and_its_workload(repo, trace, sysroot_src, write_entry_point):
    add_entry(repo, "sr", "wl", "e2", write_entry_point())
    with pytest.raises(FwmValidationError, match="has 1 trace"):
        rm.remove_entry(repo, "e")
    rm.remove_entry(repo, "e", recursive=True)
    assert Workload.select().count() == 1  # e2 still uses it
    rm.remove_entry(repo, "e2")
    assert Workload.select().count() == 0
    assert NativeEntry.select().count() == 0


def test_remove_sysroot(repo, trace, tmp_path):
    add_checkpoint(repo, "e", make_checkpoint(tmp_path, "c"))
    with pytest.raises(FwmValidationError, match="has 1 entry point"):
        rm.remove_sysroot(repo, "sr")
    rm.remove_sysroot(repo, "sr", recursive=True)
    for model in (Sysroot, Workload, NativeEntry, DecoupledTrace, DecoupledCheckpoint, TreeCatalog):
        assert model.select().count() == 0
    for d in ("sysroots", "traces", "checkpoints", ".staging"):
        assert os.listdir(os.path.join(repo.path, d)) == []


def test_remove_rolls_back(repo, trace, tmp_path, monkeypatch):
    add_checkpoint(repo, "e", make_checkpoint(tmp_path, "c"))

    def fail(*args):
        raise RuntimeError("injected")

    monkeypatch.setattr(rm, "delete_catalog", fail)
    with pytest.raises(RuntimeError):
        rm.remove_sysroot(repo, "sr", recursive=True)
    # nothing is gone: the rows are rolled back and the files moved back, read-only again
    assert Sysroot.select().count() == 1
    assert DecoupledCheckpoint.select().count() == 1
    assert os.path.isfile(repo.checkpoint_path(trace.uuid, "c"))
    assert mode(repo.sysroot_dir("sr")) == 0o555
    assert verify_sysroot(repo, Sysroot.get()) == []
    assert os.listdir(os.path.join(repo.path, ".staging")) == []
