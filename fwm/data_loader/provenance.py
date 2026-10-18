"""
The provenance of a recording (recording_provenance.json): when and on what machine it was made, the simulator's
command line, and the build information of the simulator and of its FESVR.

Written by riscv-fesvr's device_traffic_recorder_t (riscv-fesvr 8472993, riscv-isa-sim fd01c25 or later; see
riscv-isa-sim's docs/fesvr-checkpoint-and-traffic-replay.md), e.g.

    {"started": "2026-10-17T18:57:50-0400", "finished": "2026-10-17T18:59:12-0400", "time_zone": "EDT",
     "uname": {"sysname": "Linux", "nodename": "host", "release": "6.18.0", "version": "#1 SMP ...", "machine": "x86_64"},
     "command": {"argv": ["spike", "-m256", "+dev-traffic-record=rec", "/boot/pk", "app"], "cwd": "/work"},
     "simulator": {"name": "spike", "build": {"repository": "...", "version": "...", "commit": "...", ...}},
     "fesvr": {"repository": "...", "version": "...", "commit": "...", ...}}

The replay doesn't read it. Keys this model doesn't know are kept (a later spike may add some).
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pydantic

PROVENANCE_FILE = "recording_provenance.json"

# How the FESVR writes times: local time with its UTC offset, e.g. 2026-10-17T18:57:50-0400
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class ProvenanceError(ValueError):
    pass


def parse_time(text: str) -> datetime:
    """A time of the provenance, with its UTC offset."""
    try:
        return datetime.strptime(text, TIME_FORMAT)
    except ValueError:
        raise ValueError(f"{text!r} is not a local time with its UTC offset, e.g. 2026-10-17T18:57:50-0400")


class _Model(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="allow")


class Uname(_Model):
    sysname: str
    nodename: str
    release: str
    version: str
    machine: str


class Command(_Model):
    # The simulator's command line, if the simulator gave it
    argv: Optional[List[str]] = None
    cwd: str


class Simulator(_Model):
    name: str
    # The build information, as `<simulator> --version` prints it: repository, version, commit, build_date, ...
    build: Dict[str, str]


class RecordingProvenance(_Model):
    started: str
    # When the recording was closed: missing if the recording process died
    finished: Optional[str] = None
    time_zone: str
    uname: Uname
    command: Command
    # The simulator's name and build information, if the simulator gave them
    simulator: Optional[Simulator] = None
    # The build information of the FESVR (libfesvr)
    fesvr: Dict[str, str]

    @pydantic.field_validator("started", "finished")
    @classmethod
    def _time(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            parse_time(value)
        return value

    @property
    def started_at(self) -> datetime:
        return parse_time(self.started)

    @property
    def finished_at(self) -> Optional[datetime]:
        return parse_time(self.finished) if self.finished is not None else None


OLD_SPIKE = ("it was made by a spike older than riscv-isa-sim fd01c25 / riscv-fesvr 8472993, which don't record it: "
             "record it again")


def parse_provenance(text: str, where: str) -> RecordingProvenance:
    """
    :param where: The file, for messages.
    :raise ProvenanceError: The text isn't a valid provenance.
    """
    try:
        return RecordingProvenance.model_validate(json.loads(text))
    except json.JSONDecodeError as e:
        raise ProvenanceError(f"{where} is not JSON: {e}")
    except pydantic.ValidationError as e:
        raise ProvenanceError(f"invalid {where}: {e}")


def read_provenance(recording_dir: str) -> Tuple[RecordingProvenance, str]:
    """
    Read a recording's provenance.

    :return: The provenance, and the file's text.
    :raise ProvenanceError: The recording has none, or it is invalid.
    """
    path = os.path.join(recording_dir, PROVENANCE_FILE)
    if not os.path.isfile(path):
        raise ProvenanceError(f"{recording_dir} has no {PROVENANCE_FILE}: {OLD_SPIKE}")
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        raise ProvenanceError(f"cannot read {path}: {e}")
    return parse_provenance(text, path), text
