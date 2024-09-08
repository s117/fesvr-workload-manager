import json
import random
import string
from datetime import datetime
from typing import Dict, Union, Optional, List, Tuple

import pytest
from pydantic import ValidationError

random.seed(100)


def gen_random_name(len_range: range) -> str:
    assert len_range[0] > 0
    length = random.randint(len_range[0], len_range[-1])
    return "".join([random.choice(string.ascii_letters + string.digits) for _ in range(length)])


def gen_random_root(root_list: Tuple = ("", "./", "/")) -> str:
    return random.choice(root_list)


def gen_random_mode(mode_set: int = 0o000, mode_mask: int = 0o777) -> int:
    mode_rand = random.randint(0o000, 0o777)
    return mode_rand | mode_set & mode_mask


def gen_random_file_entry(
        max_path_level: int = 5,
        max_file_size: int = 4096 * 1024,
        digest_size_bytes: int = 32) -> Dict[str, Union[str, int]]:
    return {
        "path": gen_random_root() + "/".join([gen_random_name(range(3, 16)) for _ in range(max_path_level)]),
        "perm": gen_random_mode(),
        "actual_size": random.randint(0, max_file_size),
        "digest": hex(random.getrandbits(digest_size_bytes * 8))[2:].zfill(digest_size_bytes * 2).upper(),
    }


def gen_random_link_entry(max_path_level: int = 5) -> Dict[str, Union[str, int]]:
    return {
        "path": gen_random_root() + "/".join([gen_random_name(range(3, 16)) for _ in range(max_path_level)]),
        "perm": gen_random_mode(),
        "dest": gen_random_root(("", "/")) + "/".join([gen_random_name(range(3, 16)) for _ in range(max_path_level)]),
    }


def gen_random_dir_entry(max_path_level: int = 5) -> Dict[str, Union[str, int]]:
    return {
        "path": gen_random_root() + "/".join([gen_random_name(range(3, 16)) for _ in range(max_path_level)]),
        "perm": gen_random_mode(),
    }


def gen_random_file_catalog(
        algo: Optional[str] = None,
        max_file_entry: int = 10,
        max_link_entry: int = 10,
        max_dir_entry: int = 5) -> Dict[str, Union[str, List[Dict]]]:
    hash_len = {
        "md5": 128 >> 3,
        "sha256": 258 >> 3,
        "sha512": 512 >> 3,
    }
    hash_algo = algo if algo else random.choice(list(hash_len.keys()))
    num_file_entry = random.randint(1, max_file_entry)
    num_link_entry = random.randint(1, max_link_entry)
    num_dir_entry = random.randint(1, max_dir_entry)
    return {
        "creation_time": datetime.now().astimezone().isoformat(),
        "digest_algo": hash_algo,
        "files": [gen_random_file_entry(digest_size_bytes=hash_len[hash_algo]) for _ in range(num_file_entry)],
        "links": [gen_random_link_entry() for _ in range(num_link_entry)],
        "dirs": [gen_random_dir_entry() for _ in range(num_dir_entry)]
    }


@pytest.mark.parametrize("algo", ["md5", "sha256", "sha512"])
def test_file_catalog(algo):
    from fwm.data_loader.file_catalog import load_file_catalog_json
    catalog_dict = gen_random_file_catalog(algo)
    catalog = load_file_catalog_json(json.dumps(catalog_dict))
    catalog_dict["creation_time"] = datetime.fromisoformat(catalog_dict["creation_time"])
    assert catalog.model_dump() == catalog_dict


@pytest.mark.parametrize("algo", ["md5", "sha256", "sha512"])
def test_file_catalog_short_digest(algo):
    from fwm.data_loader.file_catalog import load_file_catalog_json
    catalog_dict = gen_random_file_catalog(algo)
    catalog_dict["files"][0]["digest"] = catalog_dict["files"][0]["digest"][1:]
    with pytest.raises(ValidationError) as ex:
        load_file_catalog_json(json.dumps(catalog_dict))
    assert ex.value.errors()[0]['type'] == 'assertion_error'
    assert f"contains invalid {catalog_dict["digest_algo"]} digest" in ex.value.errors()[0]['msg']


def test_file_catalog_invalid_algo_name():
    from fwm.data_loader.file_catalog import load_file_catalog_json
    catalog_dict = gen_random_file_catalog()
    catalog_dict["digest_algo"] = "bad-algo"
    with pytest.raises(ValidationError) as ex:
        load_file_catalog_json(json.dumps(catalog_dict))

    assert ex.value.errors()[0]['type'] == 'assertion_error'
    assert 'unsupported digest algorithm' in ex.value.errors()[0]['msg']


def test_file_catalog_missing_fields():
    from fwm.data_loader.file_catalog import load_file_catalog_json
    issue_type = {
        "creation_time": 'missing',
        "digest_algo": 'missing',
        "files": 'missing',
        "links": 'missing',
        "dirs": 'missing'
    }
    with pytest.raises(ValidationError) as ex:
        load_file_catalog_json("{}")

    assert len(ex.value.errors()) == len(issue_type)
    for error in ex.value.errors():
        error_field = error['loc'][0]
        assert error_field in issue_type
        assert issue_type[error_field] == error['type']  # type: ignore
        issue_type.pop(error_field)  # type: ignore
