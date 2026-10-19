"""
Validation of checkpoints: load a checkpoint, run to the end of the program, and compare with its trace.

A replay stops at the first difference from its recording (another command, other data written to the host, or a
recording that ran out). So a run from a checkpoint that ends with the program's exit (finish_reason target_exit), at
every HART's final instruction count of the trace, has replayed the rest of the program exactly, including its exit
command. Any other end fails, with its reason: e.g. an error of the simulator (a replay that diverged), a signal (a
job's time limit), or -e.
"""
import os
from typing import List, Optional, Tuple

from fwm.data_loader.final_state import read_final_state, FinalStateError, FinishReason
from fwm.database.db import now_tz
from fwm.database.schema import DecoupledCheckpoint
from fwm.mkgen.makefile import read_run_info
from fwm.repo.errors import FwmValidationError
from fwm.repo.lookup import resolve_checkpoint, trace_harts
from fwm.repo.repo import Repo

PASSED = "passed"
FAILED = "failed"
FINAL_STATE = "final-state.json"


def _judge(ckpt: DecoupledCheckpoint, run_dir: str) -> Tuple[bool, str]:
    """:return: Whether the run validates the checkpoint, and why."""
    path = os.path.join(run_dir, FINAL_STATE)
    if not os.path.isfile(path):
        return False, f"the run has no {FINAL_STATE}: it didn't run"
    try:
        state = read_final_state(path)
    except FinalStateError as e:
        return False, str(e)
    if state.finish_reason != FinishReason.TARGET_EXIT:
        where = f" at instret {state.instrets}" if state.instrets is not None else ""
        return False, f"the run didn't end with the program's exit: {state.describe()}{where}"
    expected: List[Optional[int]] = [h.instret for h in trace_harts(ckpt.trace)]
    if state.instrets != expected:
        return False, f"the run ended at instret {state.instrets}, the trace at {expected}"
    if (state.exit_code or 0) & 0xff != ckpt.trace.exit_status:
        return False, f"the program exited with code {state.exit_code}, in the trace with status {ckpt.trace.exit_status}"
    return True, f"the program exited at the trace's instret {expected}, with its exit code {state.exit_code}"


def record_validation(repo: Repo, ckpt_ref: str, run_dir: str) -> DecoupledCheckpoint:
    """
    Judge a run of a checkpoint to the end (a `fwm mkgen from-checkpoint` run directory, run without -e), and record
    the result with the checkpoint.

    :return: The checkpoint, with its validation_status (PASSED or FAILED), validation_time and validation_detail.
    :raise FwmValidationError: The run directory isn't a run of that checkpoint, or the trace's final instruction
                               counts aren't known, so nothing can be judged.
    """
    ckpt = resolve_checkpoint(ckpt_ref)
    run_info = read_run_info(run_dir)
    expected_info = {"kind": "from-checkpoint", "trace": str(ckpt.trace.uuid), "checkpoint": ckpt.name}
    if run_info != expected_info:
        raise FwmValidationError(
            f"{run_dir} is not a run directory of checkpoint {ckpt.trace.uuid}:{ckpt.name} (made by "
            f"`fwm mkgen from-checkpoint`)")
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
