import json
import os

import pytest

from fwm.database.schema import DecoupledCheckpoint
from fwm.repo.checkpoint import add_checkpoints
from fwm.repo.entry import add_entry
from fwm.repo.errors import AlreadyExistsError, FwmValidationError, NotFoundError
from fwm.repo.sysroot import add_sysroot
from fwm.repo.trace import add_trace
from tests.fesvr_files import (
    write_recording, write_checkpoint, Command, exit_command, recording_sha256, CheckpointHart, encode_hart_state,
    default_hart_state_entries
)

COMMANDS = [Command(), exit_command(0)]


@pytest.fixture
def trace(repo, sysroot_src, write_entry_point, tmp_path):
    add_sysroot(repo, str(sysroot_src), "sr")
    add_entry(repo, "sr", "wl", "e", write_entry_point())
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    return add_trace(repo, "e", str(rec))


def make(tmp_path, name, count=1000):
    path = tmp_path / "ckpts" / name
    path.parent.mkdir(exist_ok=True)
    state = encode_hart_state(default_hart_state_entries(count=count))
    write_checkpoint(str(path), [CheckpointHart(recording_sha256(COMMANDS), cmd_index=1, state=state)])
    return str(path)


def stored(repo, trace):
    return sorted(c.name for c in DecoupledCheckpoint.select().where(DecoupledCheckpoint.trace == trace))


def write_desc(tmp_path, text: str) -> str:
    path = tmp_path / "ckpt.desc"
    path.write_text(text)
    return str(path)


def checkpoint_dir(tmp_path) -> str:
    return str(tmp_path / "ckpts")


def test_import(repo, trace, tmp_path):
    make(tmp_path, "e.1.0.30", 1000)
    make(tmp_path, "e.0.0.70", 2000)
    desc = write_desc(tmp_path, "e.1.0.30: 1000\ne.0.0.70: 2003\n")
    ckpts = add_checkpoints(repo, "e", desc, checkpoint_dir(tmp_path))
    assert [c.name for c in ckpts] == ["e.1.0.30", "e.0.0.70"]  # in the order of the description
    assert [c.requested_count for c in ckpts] == [1000, 2003]
    assert [c.harts[0].csr_count for c in ckpts] == [1000, 2000]
    assert all(c.simpoint_weight is None for c in ckpts)
    assert all(os.path.isfile(repo.checkpoint_path(trace.uuid, c.name)) for c in ckpts)


def test_import_is_one_transaction(repo, trace, tmp_path):
    make(tmp_path, "good")
    (tmp_path / "ckpts" / "bad").write_bytes(b"not a checkpoint")
    desc = write_desc(tmp_path, "good: 1000\nbad: 2000\n")
    with pytest.raises(FwmValidationError, match="invalid checkpoint .*bad"):
        add_checkpoints(repo, "e", desc, checkpoint_dir(tmp_path))
    assert stored(repo, trace) == []


def test_missing_checkpoint(repo, trace, tmp_path):
    make(tmp_path, "a", 1000)
    desc = write_desc(tmp_path, "a: 1000\nb: 9000\nc: 9500\n")
    with pytest.raises(FwmValidationError, match=r"weren't created .*: b \(9000\), c \(9500\)"):
        add_checkpoints(repo, "e", desc, checkpoint_dir(tmp_path))
    assert stored(repo, trace) == []


def test_other_files_are_ignored(repo, trace, tmp_path):
    make(tmp_path, "a", 1000)
    make(tmp_path, "not-requested", 2000)
    (tmp_path / "ckpts" / "bbv_proc_0.bb.gz").write_bytes(b"bbv")
    (tmp_path / "ckpts" / "subdir").mkdir()
    ckpts = add_checkpoints(repo, "e", write_desc(tmp_path, "a: 1000\n"), checkpoint_dir(tmp_path))
    assert [c.name for c in ckpts] == ["a"]
    assert stored(repo, trace) == ["a"]


@pytest.mark.parametrize("text, error, match", [
    ("", FwmValidationError, "is empty"),
    ("a 1000\n", FwmValidationError, "invalid checkpoint description"),
    ("ckpts: 1000\n", FwmValidationError, "weren't created"),  # a directory, not a checkpoint file
])
def test_invalid_desc(repo, trace, tmp_path, text, error, match):
    make(tmp_path, "a")
    (tmp_path / "ckpts" / "ckpts").mkdir()
    with pytest.raises(error, match=match):
        add_checkpoints(repo, "e", write_desc(tmp_path, text), checkpoint_dir(tmp_path))


def test_missing_paths(repo, trace, tmp_path):
    make(tmp_path, "a")
    with pytest.raises(NotFoundError, match="cannot read the checkpoint description"):
        add_checkpoints(repo, "e", str(tmp_path / "missing.desc"), checkpoint_dir(tmp_path))
    with pytest.raises(NotFoundError, match="is not a directory"):
        add_checkpoints(repo, "e", write_desc(tmp_path, "a: 1000\n"), str(tmp_path / "missing"))


SIMPOINT_INFO = {"interval": 100000000, "maxK": 10, "checkpoints": {"e.3.0.25": {"index": 3, "weight": 0.2531},
                                                                    "e.5.0.75": {"index": 5, "weight": 0.7469}}}


def test_simpoint_info(repo, trace, tmp_path):
    make(tmp_path, "e.3.0.25", 1000)
    make(tmp_path, "e.5.0.75", 2000)
    info = tmp_path / "simpoints.json"
    info.write_text(json.dumps(SIMPOINT_INFO))
    ckpts = add_checkpoints(repo, "e", write_desc(tmp_path, "e.3.0.25: 1000\ne.5.0.75: 2000\n"),
                            checkpoint_dir(tmp_path), simpoint_info=str(info))
    a, b = (DecoupledCheckpoint.get_by_id(c.id) for c in ckpts)
    assert (a.simpoint_index, a.simpoint_interval, a.simpoint_max_k, a.simpoint_weight) == (3, 100000000, 10, 0.2531)
    assert (b.simpoint_index, b.simpoint_interval, b.simpoint_max_k, b.simpoint_weight) == (5, 100000000, 10, 0.7469)


def with_checkpoints(**checkpoints):
    return {**SIMPOINT_INFO, "checkpoints": checkpoints}


@pytest.mark.parametrize("content, match", [
    # not the checkpoints of the description
    (with_checkpoints(c={"index": 0, "weight": 1}, other={"index": 1, "weight": 0}),
     "names checkpoints that aren't imported: other"),
    (with_checkpoints(), "no SimPoint for these checkpoints: c"),
    (with_checkpoints(c={"index": 0, "weight": 0.5}, d={"index": 0, "weight": 0.5}),
     "several checkpoints have the SimPoint index 0"),
    # invalid
    ({"maxK": 10, "checkpoints": {"c": {"index": 0, "weight": 1}}}, "invalid SimPoint information"),
    ({"interval": 1, "checkpoints": {"c": {"index": 0, "weight": 1}}}, "invalid SimPoint information"),
    ({"interval": 1, "maxK": 10}, "invalid SimPoint information"),
    ({**SIMPOINT_INFO, "typo": 1}, "invalid SimPoint information"),
    ({**SIMPOINT_INFO, "interval": 0}, "invalid SimPoint information"),
    ({**SIMPOINT_INFO, "maxK": 0}, "invalid SimPoint information"),
    (with_checkpoints(c={"index": -1, "weight": 1}), "invalid SimPoint information"),
    (with_checkpoints(c={"index": 0, "weight": 1.5}), "invalid SimPoint information"),
    (with_checkpoints(c={"index": 0}), "invalid SimPoint information"),
    (with_checkpoints(c={"index": 0, "weight": 1, "typo": 2}), "invalid SimPoint information"),
    # the former schema, keyed by checkpoint name
    ({"c": {"simpoint_index": 0, "interval": 1, "weight": 1}}, "invalid SimPoint information"),
])
def test_simpoint_info_invalid(repo, trace, tmp_path, content, match):
    make(tmp_path, "c")
    make(tmp_path, "d", 2000)
    desc = write_desc(tmp_path, "c: 1000\nd: 2000\n" if "d" in content.get("checkpoints", {}) else "c: 1000\n")
    info = tmp_path / "simpoints.json"
    info.write_text(json.dumps(content))
    with pytest.raises(FwmValidationError, match=match):
        add_checkpoints(repo, "e", desc, checkpoint_dir(tmp_path), simpoint_info=str(info))
    assert stored(repo, trace) == []


def test_exist_ok(repo, trace, tmp_path):
    make(tmp_path, "a")
    add_checkpoints(repo, "e", write_desc(tmp_path, "a: 1000\n"), checkpoint_dir(tmp_path))
    make(tmp_path, "b", 2000)
    desc = write_desc(tmp_path, "a: 1000\nb: 2000\n")
    with pytest.raises(AlreadyExistsError):
        add_checkpoints(repo, "e", desc, checkpoint_dir(tmp_path))
    assert stored(repo, trace) == ["a"]
    assert [c.name for c in add_checkpoints(repo, "e", desc, checkpoint_dir(tmp_path), exist_ok=True)] == ["a", "b"]
    assert stored(repo, trace) == ["a", "b"]
