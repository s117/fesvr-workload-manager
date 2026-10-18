import json
from datetime import timedelta

import pytest

from fwm.data_loader.provenance import read_provenance, ProvenanceError, PROVENANCE_FILE
from fwm.data_loader.recording import read_recording, RecordingFormatError
from tests.fesvr_files import write_recording, provenance


def write(tmp_path, content) -> str:
    (tmp_path / PROVENANCE_FILE).write_text(content if isinstance(content, str) else json.dumps(content))
    return str(tmp_path)


def test_read(tmp_path):
    prov, text = read_provenance(write(tmp_path, provenance()))
    assert json.loads(text) == provenance()
    assert (prov.uname.nodename, prov.uname.machine, prov.command.cwd) == ("recorder", "x86_64", "/work")
    assert prov.simulator is not None and prov.simulator.build["commit"].startswith("fd01c25")
    assert prov.fesvr["commit"].startswith("8472993")
    assert prov.started_at.utcoffset() == timedelta(hours=-4)
    assert prov.finished_at is not None and prov.finished_at > prov.started_at


def test_optional_parts(tmp_path):
    """A recording whose process died has no "finished"; a simulator that gave nothing has no simulator or argv."""
    value = provenance(command={"cwd": "/work"})
    del value["finished"]
    del value["simulator"]
    prov, _ = read_provenance(write(tmp_path, value))
    assert (prov.finished, prov.finished_at, prov.simulator, prov.command.argv) == (None, None, None, None)


def test_unknown_keys_are_kept(tmp_path):
    """A later spike may add keys: they are accepted, and the file's text is kept as it is."""
    value = provenance(cpus=36)
    value["uname"]["domainname"] = "(none)"
    _, text = read_provenance(write(tmp_path, value))
    assert json.loads(text)["cpus"] == 36


@pytest.mark.parametrize("content, match", [
    ({k: v for k, v in provenance().items() if k != "uname"}, "uname"),
    (provenance(fesvr=None), "fesvr"),
    (provenance(started="2026-10-17 18:57:50"), "not a local time with its UTC offset"),
    (provenance(finished="yesterday"), "not a local time"),
    ("{", "is not JSON"),
])
def test_invalid(tmp_path, content, match):
    with pytest.raises(ProvenanceError, match=match):
        read_provenance(write(tmp_path, content))


def test_missing(tmp_path):
    with pytest.raises(ProvenanceError, match="has no recording_provenance.json: it was made by a spike older than"):
        read_provenance(str(tmp_path))


def test_a_recording_needs_its_provenance(tmp_path):
    rec = tmp_path / "rec"
    write_recording(str(rec))
    assert read_recording(str(rec)).provenance.uname.nodename == "recorder"
    write_recording(str(tmp_path / "old"), prov=None)
    with pytest.raises(RecordingFormatError, match="has no recording_provenance.json"):
        read_recording(str(tmp_path / "old"))
    write_recording(str(tmp_path / "bad"), prov="not json")
    with pytest.raises(RecordingFormatError, match="is not JSON"):
        read_recording(str(tmp_path / "bad"))
