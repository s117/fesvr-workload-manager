import json
import os

import pytest

from fwm.database.schema import DecoupledCheckpoint
from fwm.repo.checkpoint import add_checkpoints
from fwm.repo.entry import add_entry
from fwm.repo.errors import AlreadyExistsError, FwmValidationError
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


def test_batch(repo, trace, tmp_path):
    files = [make(tmp_path, "e.1.0.30", 1000), make(tmp_path, "e.0.0.70", 2000)]
    ckpts = add_checkpoints(repo, "e", files)
    assert [c.name for c in ckpts] == ["e.1.0.30", "e.0.0.70"]  # in the order of the files
    assert [c.simpoint_weight for c in ckpts] == [0.3, 0.7]
    assert all(os.path.isfile(repo.checkpoint_path(trace.uuid, c.name)) for c in ckpts)


def test_batch_is_one_transaction(repo, trace, tmp_path):
    bad = tmp_path / "ckpts" / "bad"
    files = [make(tmp_path, "good")]
    bad.write_bytes(b"not a checkpoint")
    with pytest.raises(FwmValidationError, match=f"invalid checkpoint {bad}"):
        add_checkpoints(repo, "e", files + [str(bad)])
    assert stored(repo, trace) == []


def test_desc(repo, trace, tmp_path):
    files = [make(tmp_path, "a", 1000), make(tmp_path, "b", 2000)]
    desc = tmp_path / "ckpt.desc"

    desc.write_text("a: 1000\nb: 2000\nc: 9000\n")
    with pytest.raises(FwmValidationError, match=r"weren't created .*: c \(9000\)"):
        add_checkpoints(repo, "e", files, desc=str(desc))
    desc.write_text("a: 1000\n")
    with pytest.raises(FwmValidationError, match="aren't in the description: b"):
        add_checkpoints(repo, "e", files, desc=str(desc))
    assert stored(repo, trace) == []

    desc.write_text("b: 2003\na: 1000\n")
    ckpts = add_checkpoints(repo, "e", files, desc=str(desc))
    assert [c.requested_count for c in ckpts] == [1000, 2003]


def test_simpoint_info(repo, trace, tmp_path):
    files = [make(tmp_path, "e.3.0.25", 1000), make(tmp_path, "e.5.0.75", 2000)]
    info = tmp_path / "simpoints.json"
    info.write_text(json.dumps({"e.3.0.25": {"simpoint_index": 3, "interval": 100000000, "weight": 0.2531}}))
    ckpts = add_checkpoints(repo, "e", files, simpoint_info=str(info))
    a, b = (DecoupledCheckpoint.get_by_id(c.id) for c in ckpts)
    # the weight of the information file, at full precision, over the one in the name
    assert (a.simpoint_index, a.simpoint_interval, a.simpoint_weight) == (3, 100000000, 0.2531)
    # a checkpoint without information keeps the weight in its name
    assert (b.simpoint_index, b.simpoint_interval, b.simpoint_weight) == (None, None, 0.75)


@pytest.mark.parametrize("content, match", [
    ({"other": {"simpoint_index": 0, "interval": 1}}, "names checkpoints not imported: other"),
    ({"c": {"simpoint_index": -1, "interval": 1}}, "invalid SimPoint information"),
    ({"c": {"simpoint_index": 0, "interval": 1, "weight": 1.5}}, "invalid SimPoint information"),
    ({"c": {"simpoint_index": 0}}, "invalid SimPoint information"),
    ({"c": {"simpoint_index": 0, "interval": 1, "typo": 2}}, "invalid SimPoint information"),
])
def test_simpoint_info_invalid(repo, trace, tmp_path, content, match):
    info = tmp_path / "simpoints.json"
    info.write_text(json.dumps(content))
    with pytest.raises(FwmValidationError, match=match):
        add_checkpoints(repo, "e", [make(tmp_path, "c")], simpoint_info=str(info))


def test_single_file_options(repo, trace, tmp_path):
    files = [make(tmp_path, "a"), make(tmp_path, "b", 2000)]
    with pytest.raises(FwmValidationError, match="one checkpoint file only"):
        add_checkpoints(repo, "e", files, name="x")
    with pytest.raises(FwmValidationError, match="one checkpoint file only"):
        add_checkpoints(repo, "e", files, simpoint_weight=0.5)
    with pytest.raises(FwmValidationError, match="several files would be named"):
        add_checkpoints(repo, "e", [files[0], files[0]])


def test_batch_exist_ok(repo, trace, tmp_path):
    first = make(tmp_path, "a")
    add_checkpoints(repo, "e", [first])
    files = [first, make(tmp_path, "b", 2000)]
    with pytest.raises(AlreadyExistsError):
        add_checkpoints(repo, "e", files)
    assert stored(repo, trace) == ["a"]
    assert [c.name for c in add_checkpoints(repo, "e", files, exist_ok=True)] == ["a", "b"]
    assert stored(repo, trace) == ["a", "b"]
