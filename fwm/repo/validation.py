"""
Validation of checkpoints: load a checkpoint, run to the end of the program, and compare with its trace.

A replay stops at the first difference from its recording (another command, other data written to the host, or a
recording that ran out). So a run from a checkpoint whose every HART's final instruction count equals the trace's has
replayed the rest of the program exactly, including its exit command: the exit status matches too. (spike stopped by a
signal also writes its final state, where it stopped: that count differs, so the run fails.)
"""
import json
import os
from typing import List, Optional, Tuple

import pydantic

from fwm.data_loader.final_state import read_final_state
from fwm.database.db import now_tz
from fwm.database.schema import DecoupledCheckpoint
from fwm.mkgen.makefile import read_run_info
from fwm.repo.checkpoint import resolve_checkpoint
from fwm.repo.errors import FwmValidationError
from fwm.repo.repo import Repo
from fwm.repo.trace import trace_harts

PASSED = "passed"
FAILED = "failed"
FINAL_STATE = "final-state.json"


def _judge(ckpt: DecoupledCheckpoint, run_dir: str) -> Tuple[bool, str]:
    """:return: Whether the run validates the checkpoint, and why."""
    final_state = os.path.join(run_dir, FINAL_STATE)
    if not os.path.isfile(final_state):
        return False, f"the run has no {FINAL_STATE}: it didn't run, or didn't end"
    if os.path.getsize(final_state) == 0:
        return False, (f"the run didn't end normally ({FINAL_STATE} is empty): it was stopped by -e, or by an error of "
                       f"the simulator, e.g. a replay that diverged from its recording")
    try:
        instrets = read_final_state(final_state)
    except (OSError, json.JSONDecodeError, pydantic.ValidationError) as e:
        return False, f"invalid {final_state}: {e}"
    expected: List[Optional[int]] = [h.instret for h in trace_harts(ckpt.trace)]
    if instrets != expected:
        return False, f"the run ended at instret {instrets}, the trace at {expected}"
    return True, f"the run ended at the trace's instret {expected}"


def record_validation(repo: Repo, ckpt_ref: str, run_dir: str) -> DecoupledCheckpoint:
    """
    Judge a run of a checkpoint to the end (a `fwm mkgen load` run directory, run without -e), and record the result
    with the checkpoint.

    :return: The checkpoint, with its validation_status (PASSED or FAILED), validation_time and validation_detail.
    :raise FwmValidationError: The run directory isn't a run of that checkpoint, or the trace's final instruction
                               counts aren't known, so nothing can be judged.
    """
    ckpt = resolve_checkpoint(ckpt_ref)
    run_info = read_run_info(run_dir)
    expected_info = {"kind": "load", "trace": str(ckpt.trace.uuid), "checkpoint": ckpt.name}
    if run_info != expected_info:
        raise FwmValidationError(
            f"{run_dir} is not a run directory of checkpoint {ckpt.trace.uuid}:{ckpt.name} (made by `fwm mkgen load`)")
    if any(h.instret is None for h in trace_harts(ckpt.trace)):
        raise FwmValidationError(
            f"trace {ckpt.trace.uuid} has no final instruction count to compare with: it was imported without "
            f"--final-state, or is truncated")

    passed, detail = _judge(ckpt, run_dir)
    with repo.db.atomic():
        ckpt.validation_status = PASSED if passed else FAILED
        ckpt.validation_time = now_tz()
        ckpt.validation_detail = detail
        ckpt.save()
    return ckpt
