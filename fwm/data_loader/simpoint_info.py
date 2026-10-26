"""
The SimPoint information of checkpoints, given to `fwm repo compose add checkpoint --simpoint-info`, as produced from
the SimPoint v3.2 outputs: the analysis parameters, and each checkpoint's SimPoint, by checkpoint name, e.g.

    {"interval": 100000000, "maxK": 10,
     "checkpoints": {"bfs-road-n64.3.0.25": {"index": 3, "weight": 0.2531}}}
"""
import json
from typing import Annotated, Dict

import pydantic
from annotated_types import Ge, Gt


class SimpointCheckpoint(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    # Index of the interval the checkpoint starts, among the SimPoint intervals.
    index: Annotated[int, Ge(0)]

    # The weight of the interval's cluster, in [0, 1].
    weight: Annotated[float, Ge(0), pydantic.Field(le=1)]


class SimpointInfo(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    # The interval size the analysis used, in instructions: simulate one interval with -e<interval>.
    interval: Annotated[int, Gt(0)]

    # The maximum number of clusters the analysis considered (SimPoint's -maxK).
    maxK: Annotated[int, Gt(0)]

    # Each checkpoint's SimPoint, by checkpoint name.
    checkpoints: Dict[str, SimpointCheckpoint]


def load_simpoint_info(path: str) -> SimpointInfo:
    """
    :raise OSError, json.JSONDecodeError, pydantic.ValidationError: The file can't be read or is invalid.
    """
    with open(path, "r") as f:
        return SimpointInfo.model_validate(json.load(f))
