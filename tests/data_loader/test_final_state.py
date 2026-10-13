import json

import pytest
from pydantic import ValidationError

from fwm.data_loader.final_state import read_final_state


def write(tmp_path, content) -> str:
    path = tmp_path / "final-state.json"
    path.write_text(json.dumps(content) if not isinstance(content, str) else content)
    return str(path)


def test_read_final_state(tmp_path):
    # as written by FESVR's dump_final_state()
    text = '{\n  "hart_state": [\n    {\n      "instret": 15419313\n    },\n    {\n      "instret": 7\n    }\n  ]\n}\n'
    assert read_final_state(write(tmp_path, text)) == [15419313, 7]


@pytest.mark.parametrize("content", [
    {"hart_state": []},
    {"hart_state": [{"instret": -1}]},
    {"hart_state": [{}]},
    {},
])
def test_bad_final_state(tmp_path, content):
    with pytest.raises(ValidationError):
        read_final_state(write(tmp_path, content))
