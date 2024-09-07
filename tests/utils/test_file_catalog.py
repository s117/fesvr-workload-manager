import datetime
import hashlib
import pathlib
import random
from typing import Tuple, List

import pytest

from fwm.utils.file_catalog import create_catalog

random.seed(100)


def gen_random_data(length_range: range) -> bytes:
    data = random.randbytes(random.randint(length_range[0], length_range[-1]))
    return data


def digest_data(algo: str, data: bytes) -> str:
    h = hashlib.new(algo)
    h.update(data)
    return h.hexdigest()


def digest_path(algo: str, path: str) -> str:
    with open(path, 'rb', buffering=False) as f:
        return hashlib.file_digest(f, algo).hexdigest()  # type: ignore


def gen_random_data_with_digest(digest_algo: str, length_range: range) -> Tuple[bytes, str]:
    data = gen_random_data(length_range)
    digest = digest_data(digest_algo, data)
    return data, digest


@pytest.fixture(scope='function')
def setup_test_tree(fs) -> Tuple[str, List[str], List[str], List[str]]:
    files: List[str] = []
    dirs: List[str] = []
    links: List[str] = []

    test_tree_root = pathlib.Path("/catalog_test/root")
    fs.create_dir(test_tree_root)

    # dir with one empty file
    dirs.append("test_dir1")
    test_dir1 = test_tree_root / "test_dir1"
    fs.create_dir(test_dir1)

    # dir with one randomly filled file, some links, and an empty dir
    dirs.append("test_dir2")
    test_dir2 = test_tree_root / "test_dir2"
    fs.create_dir(test_dir2)

    # empty dir
    dirs.append("test_dir2/test_dir3")
    test_dir3 = test_dir2 / "test_dir3"
    fs.create_dir(test_dir3)

    # empty file
    files.append("test_file1")
    test_file1 = test_tree_root / "test_file1"
    test_file1.touch()

    # randomly filled file
    files.append("test_dir1/test_file2")
    test_file2 = test_dir1 / "test_file2"
    with open(test_file2, "wb") as f:
        f.write(gen_random_data(range(64)))

    # absolute link to file
    links.append("test_link1")
    test_link1 = test_tree_root / "test_link1"
    test_link1.symlink_to(test_file1)

    # relative link to dir
    links.append("test_link2")
    test_link2 = test_tree_root / "test_link2"
    test_link2.symlink_to("test_dir1")

    # relative link to file
    links.append("test_dir2/test_link3")
    test_link3 = test_dir2 / "test_link3"
    test_link3.symlink_to("../test_dir1/test_file2")

    # relative link to link
    links.append("test_dir2/test_link4")
    test_link3 = test_dir2 / "test_link4"
    test_link3.symlink_to("../test_link2")

    # absolute link to link
    links.append("test_dir2/test_link5")
    test_link5 = test_dir2 / "test_link5"
    test_link5.symlink_to(test_link2)

    # relative dangling link
    links.append("test_dir2/test_link6")
    test_link6 = test_dir2 / "test_link6"
    test_link6.symlink_to("../dangling-target")

    # absolute dangling link
    links.append("test_dir2/test_link7")
    test_link7 = test_dir2 / "test_link7"
    test_link7.symlink_to("/dangling-target")

    # relative link to out-of-tree target
    links.append("test_dir2/test_link8")
    test_link8 = test_dir2 / "test_link8"
    test_link8.symlink_to("../../../usr")

    # absolute link to out-of-tree target
    links.append("test_dir2/test_link9")
    test_link9 = test_dir2 / "test_link9"
    test_link9.symlink_to("/bin")

    return str(test_tree_root), files, dirs, links


def test_create_catalog_none_exist_tree():
    with pytest.raises(FileNotFoundError):
        create_catalog("sha256", "/none-exist-path")


def test_create_catalog_not_a_tree():
    with pytest.raises(NotADirectoryError):
        create_catalog("sha256", "/dev/null")


def test_create_catalog_contain_unsupported_items():
    with pytest.raises(NotImplementedError):
        create_catalog("sha256", "/dev/cpu")


def test_create_catalog(setup_test_tree):
    root, files, dirs, links = setup_test_tree

    catalog = create_catalog("sha256", root)
    assert isinstance(catalog.creation_time, datetime.datetime)
    expected_file1 = pathlib.Path(root) / "test_file1"
    expected_file2 = pathlib.Path(root) / "test_dir1" / "test_file2"
    expect_data_catalog = {
        "creation_time": catalog.creation_time,
        "digest_algo": "sha256",
        "files": [
            {
                "path": "test_file1",
                "actual_size": expected_file1.stat().st_size,
                "digest": digest_path("sha256", str(expected_file1)),
            },
            {
                "path": "test_dir1/test_file2",
                "actual_size": expected_file2.stat().st_size,
                "digest": digest_path("sha256", str(expected_file2)),
            }
        ],
        "links": [
            {
                "path": "test_link1",
                "dest": "test_file1"
            },
            {
                "path": "test_link2",
                "dest": "test_dir1"
            },
            {
                "path": "test_dir2/test_link3",
                "dest": "test_dir1/test_file2"
            },
            {
                "path": "test_dir2/test_link6",
                "dest": "dangling-target"
            },
            {
                "path": "test_dir2/test_link7",
                "dest": "/dangling-target"
            },
            {
                "path": "test_dir2/test_link8",
                "dest": "/usr"
            },
            {
                "path": "test_dir2/test_link9",
                "dest": "/bin"
            },
            {
                "path": "test_dir2/test_link4",
                "dest": "test_dir1"
            },
            {
                "path": "test_dir2/test_link5",
                "dest": "test_dir1"
            }
        ],
        "dirs": [
            {
                "path": "test_dir1"
            },
            {
                "path": "test_dir2"
            },
            {
                "path": "test_dir2/test_dir3"
            }
        ]
    }
    assert catalog.model_dump() == expect_data_catalog
