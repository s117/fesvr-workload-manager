"""
The SimPoint information of checkpoints, given to `fwm repo add checkpoint --simpoint-info`: a JSON object keyed by
checkpoint name, e.g.

    {"bfs-road-n64.3.0.25": {"simpoint_index": 3, "weight": 0.2531, "interval": 100000000}}
"""
import json
from typing import Annotated, Dict, Optional

import pydantic
from annotated_types import Ge, Gt


class SimpointInfo(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    # Index of the interval the checkpoint starts, among the SimPoint intervals.
    simpoint_index: Annotated[int, Ge(0)]

    # The interval size the analysis used, in instructions: simulate one interval with -e<interval>.
    interval: Annotated[int, Gt(0)]

    # The weight of the interval's cluster, in [0, 1], at full precision (a checkpoint name keeps only 2 decimals).
    weight: Optional[Annotated[float, Ge(0), pydantic.Field(le=1)]] = None


SimpointInfoFile = pydantic.RootModel[Dict[str, SimpointInfo]]


def load_simpoint_info(path: str) -> Dict[str, SimpointInfo]:
    """
    :raise OSError, json.JSONDecodeError, pydantic.ValidationError: The file can't be read or is invalid.
    """
    with open(path, "r") as f:
        return SimpointInfoFile(json.load(f)).root
