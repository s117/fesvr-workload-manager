import json
from typing import Annotated, List

import pydantic
from annotated_types import Ge


class HartFinalState(pydantic.BaseModel):
    # The HART's retired-instruction count since the original boot (its CSR_COUNT).
    instret: Annotated[int, Ge(0)]


class FinalState(pydantic.BaseModel):
    """
    The JSON written by FESVR's +final-state-dump=<file> at the end of a run.
    """
    hart_state: Annotated[List[HartFinalState], pydantic.Field(min_length=1)]


def read_final_state(path: str) -> List[int]:
    """
    Read a +final-state-dump file.

    :return: The instret of each HART, by HART ID.
    """
    with open(path, "r") as f:
        state = FinalState(**json.load(f))
    return [hart.instret for hart in state.hart_state]
