"""
The final state dump (+final-state-dump=<file>): why a run finished, and each HART's retired-instruction count there.

Written by FESVR's htif_t::dump_final_state() (riscv-fesvr 6f2d3b8, riscv-isa-sim eb87256 or later; see riscv-isa-sim's
docs/fesvr-checkpoint-and-traffic-replay.md, "Final state dump"), e.g.

    {"finish_reason": "target_exit", "exit_code": 0, "hart_state": [{"instret": 3185532242}]}

The file says "unfinished" from the start of the run until its end replaces it, so it is never empty. Older spikes wrote
only {"hart_state": [...]}, or nothing, and their counts at the program's exit depended on where the run started:
such a file is refused.
"""
import enum
import json
from typing import Annotated, List, Optional

import pydantic
from annotated_types import Ge


class FinishReason(str, enum.Enum):
    TARGET_EXIT = "target_exit"                  # the program exited, with exit_code
    CHECKPOINTS_CREATED = "checkpoints_created"  # the last checkpoint of +create-checkpoint was created
    INSTRET_LIMIT = "instret_limit"              # the simulator's instruction limit (-e)
    DEBUG_QUIT = "debug_quit"                    # the user quit spike's interactive debug mode
    INTERRUPTED = "interrupted"                  # SIGTERM or SIGINT, in signal
    ERROR = "error"                              # the FESVR failed, with error; no HART state
    UNFINISHED = "unfinished"                    # the process died otherwise (a crash, kill -9); no HART state


class HartFinalState(pydantic.BaseModel):
    # The HART's retired-instruction count since the original boot (its CSR_COUNT), where the run finished.
    instret: Annotated[int, Ge(0)]


class FinalState(pydantic.BaseModel):
    finish_reason: FinishReason
    # The program's exit code (target_exit)
    exit_code: Optional[int] = None
    # The signal that stopped the run (interrupted)
    signal: Optional[str] = None
    # Why the FESVR failed (error)
    error: Optional[str] = None
    # Each HART's count, by HART ID; none after an error, or if the run never finished
    hart_state: Optional[Annotated[List[HartFinalState], pydantic.Field(min_length=1)]] = None

    @pydantic.model_validator(mode="after")
    def _consistent(self) -> "FinalState":
        no_harts = (FinishReason.ERROR, FinishReason.UNFINISHED)
        if (self.hart_state is None) != (self.finish_reason in no_harts):
            raise ValueError(f"finish_reason {self.finish_reason.value} must {'not ' * (self.finish_reason in no_harts)}"
                             f"come with hart_state")
        if (self.exit_code is None) == (self.finish_reason == FinishReason.TARGET_EXIT):
            raise ValueError("exit_code goes with finish_reason target_exit, and only with it")
        if self.finish_reason == FinishReason.ERROR and self.error is None:
            raise ValueError("finish_reason error must come with error")
        return self

    @property
    def instrets(self) -> Optional[List[int]]:
        """Each HART's count, by HART ID, if the dump has them."""
        return [h.instret for h in self.hart_state] if self.hart_state is not None else None

    def describe(self) -> str:
        """Why the run finished, in words."""
        match self.finish_reason:
            case FinishReason.TARGET_EXIT:
                return f"the program exited with code {self.exit_code}"
            case FinishReason.CHECKPOINTS_CREATED:
                return "the last checkpoint was created"
            case FinishReason.INSTRET_LIMIT:
                return "spike's instruction limit (-e) stopped it"
            case FinishReason.DEBUG_QUIT:
                return "spike's interactive debug mode was quit"
            case FinishReason.INTERRUPTED:
                return f"{self.signal or 'a signal'} stopped it (e.g. a job's time limit)"
            case FinishReason.ERROR:
                return f"the simulator failed: {self.error}"
            case FinishReason.UNFINISHED:
                return "spike died before the run finished (a crash, or a kill)"
        raise AssertionError(self.finish_reason)


class FinalStateError(ValueError):
    pass


OLD_SPIKE = ("it was written by a spike older than riscv-isa-sim eb87256 / riscv-fesvr 6f2d3b8, whose counts at the "
             "program's exit depended on where the run started: run it again with a current spike")


def read_final_state(path: str) -> FinalState:
    """
    Read a +final-state-dump file.

    :raise FinalStateError: The file can't be read, is invalid, or was written by an older spike.
    """
    try:
        with open(path, "r") as f:
            text = f.read()
    except OSError as e:
        raise FinalStateError(f"cannot read the final state {path}: {e}")
    if not text.strip():
        raise FinalStateError(f"the final state {path} is empty: {OLD_SPIKE}")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise FinalStateError(f"the final state {path} is not JSON: {e}")
    if isinstance(raw, dict) and "finish_reason" not in raw:
        raise FinalStateError(f"the final state {path} has no finish_reason: {OLD_SPIKE}")
    try:
        return FinalState.model_validate(raw)
    except pydantic.ValidationError as e:
        raise FinalStateError(f"invalid final state {path}: {e}")
