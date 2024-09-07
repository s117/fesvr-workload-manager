import json
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from fwm.data_loader.sysroot_metadata import WorkloadEntry, load_sysroot_metadata


def create_test_entry(name: Any = "test_entry", mem: Any = 1024, abs_cmd: bool = True, cmd_has_arg: bool = True,
                      abs_cwd: bool = True, abs_pk: bool = True,
                      no_stdin: bool = False, abs_stdin: bool = True, has_desc: bool = True, null_envvars: bool = False,
                      empty_envvars: bool = False) -> Dict[str, Any]:
    return {
        "name": name,
        "command": ("/app/test_cmd_abs_path.rv64" if abs_cmd else "./test_cmd_rel_path.rv64") + (
            " arg1 arg2" if cmd_has_arg else ""),
        "working_directory": "/app" if abs_cwd else "./app",
        "stdin_file": None if no_stdin else ("/app/test_stdin.data" if abs_stdin else "./test_stdin.data"),
        "envs": None if null_envvars else ([] if empty_envvars else {"VAR1": "VAL1", "VAR2": "VAL2"}),
        # "envs": None if null_envvars else ([] if empty_envvars else ["VAL1", "VAR2"]),
        "proxy_kernel": "/lib/pk" if abs_pk else "./pk",
        "mem_mb": mem,
        "description": "Sample description" if has_desc else None,
    }


def test_invalid_entry() -> None:
    with pytest.raises(ValidationError) as ex:
        WorkloadEntry(
            name=None,  # type: ignore
            command="",
            working_directory="/app",
            stdin_file=None,
            envs=None,
            proxy_kernel="/lib/pk",
            mem_mb="abc",  # type: ignore
        )
    issue_type = {
        "name": 'string_type',
        "mem_mb": 'int_parsing',
        "description": 'missing'
    }
    assert len(ex.value.errors()) == len(issue_type)
    for error in ex.value.errors():
        error_field = error['loc'][0]
        assert error_field in issue_type
        assert issue_type[error_field] == error['type']  # type: ignore
        issue_type.pop(error_field)  # type: ignore


def test_metadata_wo_workload_name(fs):
    test_file_path = "/metadata_wo_workload_name.json"
    with open(test_file_path, "w") as fp:
        json.dump({
            "sysroot_name": "test_sysroot",
            "entry_points": [create_test_entry()]
        }, fp)

    with pytest.raises(ValidationError) as ex:
        load_sysroot_metadata(test_file_path)
    assert ex.value.errors()[0]['type'] == 'missing'
    assert ex.value.errors()[0]['loc'][0] == 'workload_name'


def test_metadata_wo_sysroot_name(fs):
    test_file_path = "/metadata_wo_sysroot_name.json"
    with open(test_file_path, "w") as fp:
        json.dump({
            "workload_name": "test_workload",
            "entry_points": [create_test_entry()]
        }, fp)
    with pytest.raises(ValidationError) as ex:
        load_sysroot_metadata(test_file_path)
    assert ex.value.errors()[0]['type'] == 'missing'
    assert ex.value.errors()[0]['loc'][0] == 'sysroot_name'


def test_metadata_wo_entry_points(fs):
    test_file_path = "/metadata_wo_entry_points.json"
    with open(test_file_path, "w") as fp:
        json.dump({
            "workload_name": "test_workload",
            "sysroot_name": "test_sysroot",
        }, fp)
    with pytest.raises(ValidationError) as ex:
        load_sysroot_metadata(test_file_path)
    assert ex.value.errors()[0]['type'] == 'missing'
    assert ex.value.errors()[0]['loc'][0] == 'entry_points'


def test_metadata_normal(fs):
    test_file_path = "/metadata_normal.json"
    entries = [create_test_entry(name="entry1"), create_test_entry(name="entry2", abs_cmd=False)]

    with open(test_file_path, "w") as fp:
        json.dump({
            "sysroot_name": "test_sysroot",
            "workload_name": "test_workload",
            "entry_points": entries
        }, fp)

    metadata = load_sysroot_metadata(test_file_path)
    assert metadata.sysroot_name == "test_sysroot"
    assert metadata.workload_name == "test_workload"
    assert metadata.entry_points[0].model_dump() == entries[0]
    assert metadata.entry_points[1].model_dump() == entries[1]
