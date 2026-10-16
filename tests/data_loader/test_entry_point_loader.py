import json
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from fwm.data_loader.entry_point import WorkloadEntry, load_entry_point


def create_test_entry(mem: Any = 1024, nprocs: Any = 1, abs_cmd: bool = True, cmd_has_arg: bool = True,
                      abs_cwd: bool = True, abs_boot_elf: bool = True,
                      no_stdin: bool = False, abs_stdin: bool = True, null_envvars: bool = False,
                      empty_envvars: bool = False) -> Dict[str, Any]:
    return {
        "command": ("/app/test_cmd_abs_path.rv64" if abs_cmd else "./test_cmd_rel_path.rv64") + (
            " arg1 arg2" if cmd_has_arg else ""),
        "working_directory": "/app" if abs_cwd else "./app",
        "stdin_file": None if no_stdin else ("/app/test_stdin.data" if abs_stdin else "./test_stdin.data"),
        "envs": None if null_envvars else ({} if empty_envvars else {"VAR1": "VAL1", "VAR2": "VAL2"}),
        "boot_elf": "/lib/pk" if abs_boot_elf else "./pk",
        "mem_mb": mem,
        "nprocs": nprocs,
    }


def test_invalid_entry() -> None:
    with pytest.raises(ValidationError) as ex:
        WorkloadEntry(
            command="",
            working_directory="/app",
            stdin_file=None,
            boot_elf=None,  # type: ignore
            mem_mb="abc",  # type: ignore
        )  # type: ignore
    issue_type = {
        "boot_elf": 'string_type',
        "mem_mb": 'int_parsing',
        "envs": 'missing',
        "nprocs": 'missing',
    }
    assert len(ex.value.errors()) == len(issue_type)
    for error in ex.value.errors():
        error_field = error['loc'][0]
        assert error_field in issue_type
        assert issue_type[error_field] == error['type']  # type: ignore
        issue_type.pop(error_field)  # type: ignore


@pytest.mark.parametrize("nprocs", [0, 2, "1"])
def test_entry_only_one_proc(nprocs) -> None:
    with pytest.raises(ValidationError) as ex:
        WorkloadEntry(**create_test_entry(nprocs=nprocs))
    assert [error['loc'][0] for error in ex.value.errors()] == ["nprocs"]


@pytest.mark.parametrize("mem", [0, -1])
def test_entry_mem_positive(mem) -> None:
    with pytest.raises(ValidationError) as ex:
        WorkloadEntry(**create_test_entry(mem=mem))
    assert [error['loc'][0] for error in ex.value.errors()] == ["mem_mb"]


@pytest.mark.parametrize("stale_field", ["name", "description", "proxy_kernel"])
def test_entry_rejects_unknown_fields(stale_field) -> None:
    entry = create_test_entry()
    entry[stale_field] = "x"
    with pytest.raises(ValidationError) as ex:
        WorkloadEntry(**entry)
    assert ex.value.errors()[0]['type'] == 'extra_forbidden'
    assert ex.value.errors()[0]['loc'][0] == stale_field


def test_entry_point_missing_field(fs):
    test_file_path = "/entry_point_wo_boot_elf.json"
    entry = create_test_entry()
    entry.pop("boot_elf")
    with open(test_file_path, "w") as fp:
        json.dump(entry, fp)

    with pytest.raises(ValidationError) as ex:
        load_entry_point(test_file_path)
    assert ex.value.errors()[0]['type'] == 'missing'
    assert ex.value.errors()[0]['loc'][0] == 'boot_elf'


@pytest.mark.parametrize("entry", [
    create_test_entry(),
    create_test_entry(abs_cmd=False, abs_cwd=False, abs_boot_elf=False, abs_stdin=False),
    create_test_entry(no_stdin=True, null_envvars=True, cmd_has_arg=False),
    create_test_entry(empty_envvars=True),
])
def test_entry_point_normal(fs, entry):
    test_file_path = "/entry_point_normal.json"
    with open(test_file_path, "w") as fp:
        json.dump(entry, fp)

    assert load_entry_point(test_file_path).model_dump() == {**entry, "metadata": None}


METADATA = {"suite": "GAPBS v1.5-0-gb5e3e19", "description": "BFS on 'road'.", "language": ["C++"],
            "auxiliary": {"validator": {"stdout": "x.out", "verify": [{"cmd": "diff a b"}]}}}


@pytest.mark.parametrize("metadata", [None, {}, METADATA])
def test_entry_point_metadata(fs, metadata):
    with open("/entry_point.json", "w") as fp:
        json.dump({**create_test_entry(), "metadata": metadata}, fp)
    assert load_entry_point("/entry_point.json").metadata == metadata


@pytest.mark.parametrize("metadata", ["GAPBS", ["C++"], 1])
def test_entry_point_metadata_not_object(metadata):
    with pytest.raises(ValidationError) as ex:
        WorkloadEntry(**create_test_entry(), metadata=metadata)
    assert [error['loc'][0] for error in ex.value.errors()] == ["metadata"]


def test_entry_point_not_object(fs):
    with open("/entry_point.json", "w") as fp:
        json.dump([create_test_entry()], fp)
    with pytest.raises(ValidationError):
        load_entry_point("/entry_point.json")
