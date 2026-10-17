import json

import pytest

from fwm.data_loader.final_state import read_final_state, FinalStateError, FinishReason


def write(tmp_path, content) -> str:
    path = tmp_path / "final-state.json"
    path.write_text(json.dumps(content) if not isinstance(content, str) else content)
    return str(path)


def test_read_final_state(tmp_path):
    # as written by FESVR's dump_final_state()
    text = ('{\n  "finish_reason": "target_exit",\n  "exit_code": 3,\n  "hart_state": [\n    {\n      "instret": 15419313\n'
            '    },\n    {\n      "instret": 7\n    }\n  ]\n}\n')
    state = read_final_state(write(tmp_path, text))
    assert (state.finish_reason, state.exit_code, state.instrets) == (FinishReason.TARGET_EXIT, 3, [15419313, 7])
    assert state.describe() == "the program exited with code 3"


@pytest.mark.parametrize("content, instrets, words", [
    ({"finish_reason": "checkpoints_created", "hart_state": [{"instret": 5}]}, [5], "last checkpoint"),
    ({"finish_reason": "instret_limit", "hart_state": [{"instret": 5}]}, [5], "-e"),
    ({"finish_reason": "debug_quit", "hart_state": [{"instret": 5}]}, [5], "debug"),
    ({"finish_reason": "interrupted", "signal": "SIGTERM", "hart_state": [{"instret": 5}]}, [5], "SIGTERM stopped it"),
    ({"finish_reason": "error", "error": "the replay diverged"}, None, "failed: the replay diverged"),
    ({"finish_reason": "unfinished"}, None, "died"),
])
def test_finish_reasons(tmp_path, content, instrets, words):
    state = read_final_state(write(tmp_path, content))
    assert state.instrets == instrets
    assert words in state.describe()


@pytest.mark.parametrize("content, match", [
    ("", "is empty: it was written by a spike older than"),
    ({"hart_state": [{"instret": 5}]}, "has no finish_reason: it was written by a spike older than"),
    ("{", "is not JSON"),
    ({"finish_reason": "target_exit", "hart_state": [{"instret": 5}]}, "exit_code goes with"),
    ({"finish_reason": "instret_limit", "exit_code": 0, "hart_state": [{"instret": 5}]}, "exit_code goes with"),
    ({"finish_reason": "error", "error": "x", "hart_state": [{"instret": 5}]}, "must not come with hart_state"),
    ({"finish_reason": "error"}, "must come with error"),
    ({"finish_reason": "interrupted"}, "must come with hart_state"),
    ({"finish_reason": "target_exit", "exit_code": 0, "hart_state": []}, "invalid final state"),
    ({"finish_reason": "target_exit", "exit_code": 0, "hart_state": [{"instret": -1}]}, "invalid final state"),
    ({"finish_reason": "finished"}, "invalid final state"),
])
def test_bad_final_state(tmp_path, content, match):
    with pytest.raises(FinalStateError, match=match):
        read_final_state(write(tmp_path, content))


def test_missing_final_state(tmp_path):
    with pytest.raises(FinalStateError, match="cannot read"):
        read_final_state(str(tmp_path / "none.json"))
