import json
import os
import uuid
from types import SimpleNamespace

import pytest

from fwm.database.schema import (
    Sysroot, Workload, NativeEntry, DecoupledTrace, TraceHart, DecoupledCheckpoint, CheckpointHart, TreeCatalog
)
from fwm.repo import remove as rm
from fwm.repo.checkpoint import verify_checkpoint
from fwm.repo.entry import add_entry, set_entry_metadata
from fwm.repo.errors import AlreadyExistsError, FwmValidationError, NotFoundError, RepoUnusableError
from fwm.repo.lookup import entry_metadata, resolve_checkpoint, resolve_trace
from fwm.repo.repo import Repo
from fwm.repo.sysroot import add_sysroot, verify_sysroot
from fwm.repo.trace import add_trace, verify_trace
from tests.data_loader.test_sim_version import SPIKE_VERSION
from tests.fesvr_files import (
    provenance,
    final_state_json,
    write_recording, write_checkpoint, Command, exit_command, recording_sha256, CheckpointHart as CkptHart,
    encode_hart_state, default_hart_state_entries
)
from tests.conftest import import_checkpoint, mode

COMMANDS = [Command(), Command(), exit_command(0)]
OTHER_COMMANDS = [Command(payload=0x2000), exit_command(0)]


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
    with pytest.raises(RepoUnusableError):
        Repo.open(str(tmp_path))


def test_open_unbound(tmp_path):
    """A second repository opened unbound leaves the models with the first one, until bound()."""
    first = Repo.init(str(tmp_path / "a"))
    Workload.create(name="in-a")
    Repo.init(str(tmp_path / "b"), bind=False).close()
    second = Repo.open(str(tmp_path / "b"), readonly=True, bind=False)
    assert [w.name for w in Workload.select()] == ["in-a"]
    with second.bound():
        assert list(Workload.select()) == []
    with pytest.raises(ZeroDivisionError):
        with second.bound():
            1 / 0
    assert [w.name for w in Workload.select()] == ["in-a"]
    second.close()
    first.close()


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
    sysroot = add_sysroot(repo, str(sysroot_src), "sr")
    with pytest.raises(AlreadyExistsError, match="already exists"):
        add_sysroot(repo, str(sysroot_src), "sr")
    # the same tree is accepted with exist_ok...
    assert add_sysroot(repo, str(sysroot_src), "sr", exist_ok=True).name == sysroot.name
    # ...a different one isn't
    (sysroot_src / "app" / "input.txt").chmod(0o644)
    (sysroot_src / "app" / "input.txt").write_text("changed\n")
    with pytest.raises(AlreadyExistsError, match="different content: 1 path.*app/input.txt"):
        add_sysroot(repo, str(sysroot_src), "sr", exist_ok=True)


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
    ({"command": 'prog "unclosed'}, "cannot split the command"),
    ({"nprocs": 2}, "invalid entry point"),
    ({"proxy_kernel": "/boot/pk"}, "invalid entry point"),
    ({"metadata": "GAPBS"}, "invalid entry point"),
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
    add_entry(repo, "sr", "wl", "e", write_entry_point(), description="first")
    with pytest.raises(AlreadyExistsError, match="already exists"):
        add_entry(repo, "sr", "wl", "e", write_entry_point())
    # the same entry point (its description aside) is accepted with exist_ok
    assert add_entry(repo, "sr", "wl", "e", write_entry_point(), description="other", exist_ok=True).description == \
        "first"
    with pytest.raises(AlreadyExistsError, match="differs"):
        add_entry(repo, "sr", "wl", "e", write_entry_point(mem_mb=2), exist_ok=True)
    with pytest.raises(AlreadyExistsError, match="differs"):
        add_entry(repo, "sr", "other-wl", "e", write_entry_point(), exist_ok=True)


def test_entry_metadata(repo, sysroot_src, write_entry_point, caplog):
    add_sysroot(repo, str(sysroot_src), "sr")
    metadata = {"suite": "GAPBS", "language": ["C++"], "auxiliary": {"validator": {"stdout": "x.out"}}}
    assert entry_metadata(add_entry(repo, "sr", "wl", "e", write_entry_point(metadata=metadata))) == metadata
    assert entry_metadata(NativeEntry.get(NativeEntry.name == "e")) == metadata
    assert entry_metadata(add_entry(repo, "sr", "wl", "plain", write_entry_point())) is None

    # metadata doesn't make an entry point different: the stored one is kept, with a warning
    again = add_entry(repo, "sr", "wl", "e", write_entry_point(metadata={"suite": "other"}), exist_ok=True)
    assert entry_metadata(again) == metadata
    assert "set-metadata" in caplog.text
    caplog.clear()
    add_entry(repo, "sr", "wl", "e", write_entry_point(metadata=metadata), exist_ok=True)
    assert "set-metadata" not in caplog.text


def test_set_entry_metadata(repo, sysroot_src, write_entry_point, tmp_path):
    add_sysroot(repo, str(sysroot_src), "sr")
    add_entry(repo, "sr", "wl", "e", write_entry_point(metadata={"suite": "a"}))
    path = tmp_path / "metadata.json"
    path.write_text('{"suite": "b", "language": ["C"]}')
    assert entry_metadata(set_entry_metadata(repo, "e", str(path))) == {"suite": "b", "language": ["C"]}
    assert entry_metadata(NativeEntry.get(NativeEntry.name == "e")) == {"suite": "b", "language": ["C"]}
    path.write_text("null")
    assert entry_metadata(set_entry_metadata(repo, "e", str(path))) is None

    for bad, match in [("[1]", "not a JSON object"), ("{", "cannot read"), ("", "cannot read")]:
        path.write_text(bad)
        with pytest.raises(FwmValidationError, match=match):
            set_entry_metadata(repo, "e", str(path))
    with pytest.raises(FwmValidationError, match="cannot read"):
        set_entry_metadata(repo, "e", str(tmp_path / "missing.json"))
    path.write_text("{}")
    with pytest.raises(NotFoundError):
        set_entry_metadata(repo, "nope", str(path))


def test_add_entry_missing_sysroot(repo, write_entry_point):
    with pytest.raises(NotFoundError):
        add_entry(repo, "nope", "wl", "e", write_entry_point())


# --- traces


@pytest.fixture
def entry(repo, sysroot_src, write_entry_point):
    add_sysroot(repo, str(sysroot_src), "sr")
    return add_entry(repo, "sr", "wl", "e", write_entry_point())


def write_final_state(tmp_path, *instrets, exit_code=0) -> str:
    path = tmp_path / "final-state.json"
    path.write_text(final_state_json(*instrets, exit_code=exit_code))
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


def test_add_trace_nonzero_exit(repo, entry, tmp_path, caplog):
    """A program that exits with an error status is a complete trace; the status is kept, with a warning."""
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[[Command(), exit_command(3)]])
    trace = add_trace(repo, "e", str(rec), final_state=write_final_state(tmp_path, 900, exit_code=3))
    assert trace.exit_status == 3
    assert "exited with status 3" in caplog.text


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


def test_add_trace_again(repo, entry, tmp_path):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    final_state = tmp_path / "final-state.json"
    final_state.write_text(final_state_json(5000))
    trace = add_trace(repo, "e", str(rec), final_state=str(final_state))

    # the same recording isn't imported twice
    with pytest.raises(AlreadyExistsError, match=f"already is trace {trace.uuid}"):
        add_trace(repo, "e", str(rec), final_state=str(final_state))
    assert add_trace(repo, "e", str(rec), final_state=str(final_state), exist_ok=True).uuid == trace.uuid
    # with exist_ok, only with the same final state
    with pytest.raises(AlreadyExistsError, match="different final state"):
        add_trace(repo, "e", str(rec), exist_ok=True)
    final_state.write_text(final_state_json(6000))
    with pytest.raises(AlreadyExistsError, match="different final state"):
        add_trace(repo, "e", str(rec), final_state=str(final_state), exist_ok=True)
    assert DecoupledTrace.select().count() == 1
    assert os.listdir(os.path.join(repo.path, "traces")) == [str(trace.uuid)]


def test_add_trace_interrupted_run(repo, entry, tmp_path):
    """spike stopped by SIGTERM (e.g. a job's time limit) says so in its final state."""
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[[Command(), Command()]])
    final_state = tmp_path / "final-state.json"
    final_state.write_text(final_state_json(1234, reason="interrupted", signal="SIGTERM"))
    with pytest.raises(FwmValidationError, match="SIGTERM stopped it"):
        add_trace(repo, "e", str(rec), final_state=str(final_state))
    assert add_trace(repo, "e", str(rec)).exit_status is None


def test_resolve_trace(repo, entry, tmp_path, monkeypatch):
    uuids = iter([uuid.UUID("12345678-0000-0000-0000-000000000001"), uuid.UUID("12345678-0000-0000-0000-000000000002")])
    monkeypatch.setattr("fwm.repo.trace.uuid", SimpleNamespace(uuid4=lambda: next(uuids)))
    rec = tmp_path / "rec"
    write_recording(str(rec))
    trace = add_trace(repo, "e", str(rec))
    assert resolve_trace("e").uuid == trace.uuid
    assert resolve_trace(str(trace.uuid)).uuid == trace.uuid
    assert resolve_trace("12345678-0000-0000-0000-00000000000").uuid == trace.uuid

    rec2 = tmp_path / "rec2"
    write_recording(str(rec2), harts=[OTHER_COMMANDS])
    add_trace(repo, "e", str(rec2))
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
    path.parent.mkdir(parents=True, exist_ok=True)
    state = encode_hart_state(default_hart_state_entries(count=count))
    write_checkpoint(str(path), [CkptHart(rec_sha or recording_sha256(COMMANDS), cmd_index=cmd_index, state=state)],
                     mem_mb=1, **kwargs)
    return str(path)


def test_import_checkpoint(repo, trace, tmp_path):
    file = make_checkpoint(tmp_path, "e.3.0.25", cmd_index=2, count=777)
    ckpt = import_checkpoint(repo, "e", file, description="c")
    assert ckpt.name == "e.3.0.25"
    # a name is just a name: no SimPoint weight is decoded from it
    assert (ckpt.simpoint_weight, ckpt.simpoint_index, ckpt.simpoint_interval, ckpt.simpoint_max_k) == (None,) * 4
    assert ckpt.requested_count == 777
    assert ckpt.format_version == 1
    assert len(ckpt.content_sha256) == 64
    assert [(h.hart_id, h.cmd_index, h.csr_count, h.hart_state_version) for h in ckpt.harts] == [(0, 2, 777, 1)]
    stored = repo.checkpoint_path(trace.uuid, "e.3.0.25")
    assert mode(stored) == 0o444
    assert verify_checkpoint(repo, ckpt) == []


@pytest.mark.parametrize("ckpt_kwargs, match", [
    ({"rec_sha": "ab" * 32}, "created from the recording"),
    ({"cmd_index": 4}, "resumes at command 4"),
    ({"boot_elf": b"other"}, "boot ELF has SHA256"),
])
def test_add_checkpoint_wrong_trace(repo, trace, tmp_path, ckpt_kwargs, match):
    with pytest.raises(FwmValidationError, match=match):
        import_checkpoint(repo, "e", make_checkpoint(tmp_path, "c", **ckpt_kwargs))
    assert DecoupledCheckpoint.select().count() == 0


def test_add_checkpoint_other_system(repo, trace, tmp_path):
    """A checkpoint of a system of another size or HART count doesn't belong to the trace."""
    harts = [CkptHart(recording_sha256(COMMANDS), cmd_index=1)]
    bigger = str(tmp_path / "bigger" / "c")
    os.makedirs(os.path.dirname(bigger))
    write_checkpoint(bigger, harts, mem_mb=2)
    with pytest.raises(FwmValidationError, match="it is of a 2 MB system, the trace of a 1 MB one"):
        import_checkpoint(repo, "e", bigger)
    two_harts = str(tmp_path / "two" / "c")
    os.makedirs(os.path.dirname(two_harts))
    write_checkpoint(two_harts, harts * 2, mem_mb=1)
    with pytest.raises(FwmValidationError, match=r"it has 2 HART\(s\), the trace has 1"):
        import_checkpoint(repo, "e", two_harts)
    assert DecoupledCheckpoint.select().count() == 0


def test_add_checkpoint_corrupt(repo, trace, tmp_path):
    file = make_checkpoint(tmp_path, "c", trailing=b"junk")
    with pytest.raises(FwmValidationError, match="invalid checkpoint"):
        import_checkpoint(repo, "e", file)


def test_add_checkpoint_duplicate(repo, trace, tmp_path):
    file = make_checkpoint(tmp_path, "c")
    ckpt = import_checkpoint(repo, "e", file)
    with pytest.raises(AlreadyExistsError, match="already has a checkpoint"):
        import_checkpoint(repo, "e", file)
    assert import_checkpoint(repo, "e", file, exist_ok=True).content_sha256 == ckpt.content_sha256
    other = make_checkpoint(tmp_path / "other", "c", count=2000)
    with pytest.raises(AlreadyExistsError, match="different content"):
        import_checkpoint(repo, "e", other, exist_ok=True)


def test_add_checkpoint_sim_version(repo, trace, tmp_path):
    ckpt = import_checkpoint(repo, "e", make_checkpoint(tmp_path, "c"), sim_version=SPIKE_VERSION)
    assert DecoupledCheckpoint.get_by_id(ckpt.id).sim_version == SPIKE_VERSION


def test_resolve_checkpoint(repo, entry, trace, tmp_path):
    file = make_checkpoint(tmp_path, "c")
    import_checkpoint(repo, "e", file)
    assert resolve_checkpoint("c").trace.uuid == trace.uuid
    assert resolve_checkpoint(f"{trace.uuid}:c").name == "c"

    # another trace with a checkpoint of the same name
    rec2 = tmp_path / "rec2"
    write_recording(str(rec2), harts=[OTHER_COMMANDS])
    trace2 = add_trace(repo, "e", str(rec2))
    import_checkpoint(repo, str(trace2.uuid), make_checkpoint(tmp_path / "other", "c",
                                                           rec_sha=recording_sha256(OTHER_COMMANDS)))
    with pytest.raises(FwmValidationError, match="give it as <trace>:<name>"):
        resolve_checkpoint("c")
    assert resolve_checkpoint(f"{trace2.uuid.hex[:12]}:c").trace.uuid == trace2.uuid
    with pytest.raises(NotFoundError):
        resolve_checkpoint("missing")


# --- removal


def test_remove_checkpoint(repo, trace, tmp_path):
    import_checkpoint(repo, "e", make_checkpoint(tmp_path, "c"))
    rm.remove_checkpoint(repo, "c")
    assert DecoupledCheckpoint.select().count() == 0
    assert CheckpointHart.select().count() == 0
    assert not os.path.exists(repo.checkpoint_path(trace.uuid, "c"))


def test_remove_trace(repo, trace, tmp_path):
    import_checkpoint(repo, "e", make_checkpoint(tmp_path, "c"))
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
    import_checkpoint(repo, "e", make_checkpoint(tmp_path, "c"))
    with pytest.raises(FwmValidationError, match="has 1 entry point"):
        rm.remove_sysroot(repo, "sr")
    rm.remove_sysroot(repo, "sr", recursive=True)
    for model in (Sysroot, Workload, NativeEntry, DecoupledTrace, DecoupledCheckpoint, TreeCatalog):
        assert model.select().count() == 0
    for d in ("sysroots", "traces", "checkpoints", ".staging"):
        assert os.listdir(os.path.join(repo.path, d)) == []


def test_remove_rolls_back(repo, trace, tmp_path, monkeypatch):
    import_checkpoint(repo, "e", make_checkpoint(tmp_path, "c"))

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


# --- inputs that can't be imported


def test_unreadable_file(repo, sysroot_src):
    (sysroot_src / "app" / "data" / "private").chmod(0o000)
    with pytest.raises(FwmValidationError, match="cannot copy"):
        add_sysroot(repo, str(sysroot_src), "sr")
    assert os.listdir(os.path.join(repo.path, ".staging")) == []


def test_non_utf8_name(repo, sysroot_src):
    with open(os.path.join(os.fsencode(str(sysroot_src)), b"\xff-name"), "w") as f:
        f.write("x")
    with pytest.raises(FwmValidationError, match="is not UTF-8"):
        add_sysroot(repo, str(sysroot_src), "sr")


def test_fifo_in_trace(repo, entry, tmp_path):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    os.mkfifo(rec / "fifo")
    with pytest.raises(FwmValidationError, match="neither a file nor symlink"):
        add_trace(repo, "e", str(rec))


def test_add_trace_stores_its_provenance(repo, entry, tmp_path, caplog):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    trace = DecoupledTrace.get_by_id(add_trace(repo, "e", str(rec)).id)
    assert json.loads(trace.provenance) == provenance()
    assert trace.recorded_on == "recorder"
    assert (trace.recording_started.isoformat(), trace.recording_finished.isoformat()) == (
        "2026-10-17T18:57:50-04:00", "2026-10-17T18:59:12-04:00")
    assert "finished" not in caplog.text


def test_provenance_without_its_end(repo, entry, tmp_path, caplog):
    """A complete recording whose process died before noting when it finished: imported, with a warning."""
    rec = tmp_path / "rec"
    prov = provenance()
    del prov["finished"]
    write_recording(str(rec), harts=[COMMANDS], prov=prov)
    assert add_trace(repo, "e", str(rec)).recording_finished is None
    assert "doesn't say when it finished" in caplog.text


def test_same_recording_another_provenance(repo, entry, tmp_path):
    """The same traffic recorded again (another time, another host) is the same trace, which keeps its provenance."""
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    trace = add_trace(repo, "e", str(rec))
    again = tmp_path / "again"
    write_recording(str(again), harts=[COMMANDS], prov=provenance(uname={**provenance()["uname"], "nodename": "other"}))
    assert add_trace(repo, "e", str(again), exist_ok=True).uuid == trace.uuid
    assert DecoupledTrace.get_by_id(trace.id).recorded_on == "recorder"
