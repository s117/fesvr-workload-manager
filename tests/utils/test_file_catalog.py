import datetime
import hashlib
import pathlib
import random
from typing import Tuple, List, Any, Dict, Union

import pytest

from fwm.data_loader.file_catalog import FileCatalog
from fwm.utils.file_catalog import create_catalog, verify_catalog

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
def setup_test_tree(fs) -> Tuple[str, Dict[str, Any]]:
    files: List[Dict[str, Union[str, int]]] = []
    dirs: List[Dict[str, Union[str, int]]] = []
    links: List[Dict[str, Union[str, int]]] = []
    expect_data_catalog = {
        "creation_time": datetime.datetime.now().astimezone(),
        "digest_algo": "sha256",
        "files": files,
        "links": links,
        "dirs": dirs
    }

    def add_dir(path):
        dirs.append({
            "path": str(path),
            "perm": (test_tree_root / path).stat().st_mode & 0o7777
        })

    def add_file(path):
        stat = (test_tree_root / path).stat()
        files.append({
            "path": str(path),
            "perm": stat.st_mode & 0o777,
            "actual_size": stat.st_size,
            "digest": digest_path("sha256", test_tree_root / path),
        })

    def add_link(path, resolve_to):
        links.append({
            "path": str(path),
            "perm": (test_tree_root / path).stat(follow_symlinks=False).st_mode & 0o777,
            "dest": resolve_to
        })

    test_tree_root = pathlib.Path("/catalog_test/root")
    fs.create_dir(test_tree_root)

    # dir with one empty file
    test_dir1 = test_tree_root / "test_dir1"
    fs.create_dir(test_dir1)
    add_dir(test_dir1.relative_to(test_tree_root))

    # dir with two randomly filled file, some links, and an empty dir
    test_dir2 = test_tree_root / "test_dir2"
    fs.create_dir(test_dir2)
    add_dir(test_dir2.relative_to(test_tree_root))

    # empty dir
    test_dir3 = test_dir2 / "test_dir3"
    fs.create_dir(test_dir3)
    add_dir(test_dir3.relative_to(test_tree_root))

    # empty file
    test_file1 = test_tree_root / "test_file1"
    test_file1.touch()
    add_file(test_file1.relative_to(test_tree_root))

    # randomly filled file
    test_file2 = test_dir2 / "test_file2"
    with open(test_file2, "wb") as f:
        f.write(gen_random_data(range(64)))
    add_file(test_file2.relative_to(test_tree_root))

    # another randomly filled file
    test_file3 = test_dir2 / "test_file3"
    with open(test_file3, "wb") as f:
        f.write(gen_random_data(range(64)))
    add_file(test_file3.relative_to(test_tree_root))

    # absolute link to file
    test_link1 = test_tree_root / "test_link1"
    test_link1.symlink_to(test_file1)
    add_link(test_link1.relative_to(test_tree_root), "test_file1")

    # relative link to dir
    test_link2 = test_tree_root / "test_link2"
    test_link2.symlink_to("test_dir1")
    add_link(test_link2.relative_to(test_tree_root), "test_dir1")

    # relative link to file
    test_link3 = test_dir2 / "test_link3"
    test_link3.symlink_to("../test_dir1/test_file2")
    add_link(test_link3.relative_to(test_tree_root), "test_dir1/test_file2")

    # relative dangling link
    test_link6 = test_dir2 / "test_link6"
    test_link6.symlink_to("../dangling-target")
    add_link(test_link6.relative_to(test_tree_root), "dangling-target")

    # absolute dangling link
    test_link7 = test_dir2 / "test_link7"
    test_link7.symlink_to("/dangling-target")
    add_link(test_link7.relative_to(test_tree_root), "/dangling-target")

    # relative link to out-of-tree target
    test_link8 = test_dir2 / "test_link8"
    test_link8.symlink_to("../../../usr")
    add_link(test_link8.relative_to(test_tree_root), "/usr")

    # absolute link to out-of-tree target
    test_link9 = test_dir2 / "test_link9"
    test_link9.symlink_to("/bin")
    add_link(test_link9.relative_to(test_tree_root), "/bin")

    # relative link to link
    test_link4 = test_dir2 / "test_link4"
    test_link4.symlink_to("../test_link2")
    add_link(test_link4.relative_to(test_tree_root), "test_dir1")

    # absolute link to link
    test_link5 = test_dir2 / "test_link5"
    test_link5.symlink_to(test_link2)
    add_link(test_link5.relative_to(test_tree_root), "test_dir1")

    return str(test_tree_root), expect_data_catalog


def test_create_catalog_none_exist_tree():
    with pytest.raises(FileNotFoundError):
        create_catalog("sha256", "/none-exist-path")


def test_create_catalog_not_a_tree():
    with pytest.raises(NotADirectoryError):
        create_catalog("sha256", "/dev/null")


def test_create_catalog_contain_unsupported_items():
    with pytest.raises(NotImplementedError):
        create_catalog("sha256", "/dev")


def test_create_catalog(setup_test_tree):
    root, expect_data_catalog = setup_test_tree

    catalog = create_catalog("sha256", root)
    assert isinstance(catalog.creation_time, datetime.datetime)
    expect_data_catalog["creation_time"] = catalog.creation_time
    # print(catalog.model_dump_json(indent=2))
    assert catalog.model_dump() == expect_data_catalog


def test_verify_catalog_normal(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)

    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert file_problems == []
    assert link_problems == []
    assert dir_problems == []


def test_verify_catalog_dir_missing(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_dir3"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_dir()

    test_target_path.rmdir()
    assert not test_target_path.exists()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert file_problems == []
    assert link_problems == []
    assert dir_problems == [(test_target, "directory missing")]


def test_verify_catalog_dir_not_a_dir(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_dir3"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_dir()

    test_target_path.rmdir()
    test_target_path.touch()
    assert test_target_path.is_file()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert file_problems == []
    assert link_problems == []
    assert dir_problems == [(test_target, "not a directory")]

    # is a link to a file
    test_target_path.unlink()
    test_target_path.symlink_to("../test_file1")
    assert test_target_path.is_symlink()
    assert test_target_path.exists(follow_symlinks=True)
    assert test_target_path.is_file()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert file_problems == []
    assert link_problems == []
    assert dir_problems == [(test_target, "not a directory")]

    # is a link to a dir
    test_target_path.unlink()
    test_target_path.symlink_to("../test_dir1")
    assert test_target_path.is_symlink()
    assert test_target_path.exists(follow_symlinks=True)
    assert test_target_path.is_dir()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert file_problems == []
    assert link_problems == []
    assert dir_problems == [(test_target, "not a directory")]


def test_verify_catalog_dir_mode_mismatch(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_dir3"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_dir()

    test_target_path.chmod(0o000)
    assert test_target_path.is_dir()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert file_problems == []
    assert link_problems == []
    assert len(dir_problems) == 1
    assert dir_problems[0][0] == test_target
    assert dir_problems[0][1].startswith("directory mode mismatch")
    assert "000" in dir_problems[0][1]


def test_verify_catalog_link_missing(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_link3"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_symlink()

    test_target_path.unlink()
    assert not test_target_path.exists()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert link_problems == [(test_target, "link missing")]
    assert file_problems == []
    assert dir_problems == []


def test_verify_catalog_link_not_a_link(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_link3"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_symlink()

    # is a file
    test_target_path.unlink()
    test_target_path.touch()
    assert test_target_path.is_file()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert dir_problems == []
    assert file_problems == []
    assert link_problems == [(test_target, "not a link")]

    # is a dir
    test_target_path.unlink()
    test_target_path.mkdir()
    assert test_target_path.is_dir()
    assert dir_problems == []
    assert file_problems == []
    assert link_problems == [(test_target, "not a link")]


def test_verify_catalog_link_mode_mismatch(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_link3"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_symlink()

    test_target_path.chmod(0o000, follow_symlinks=False)
    assert test_target_path.is_symlink()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert file_problems == []
    assert dir_problems == []
    assert len(link_problems) == 1
    assert link_problems[0][0] == test_target
    assert link_problems[0][1].startswith("link mode mismatch")
    assert "000" in link_problems[0][1]


def test_verify_catalog_link_dest_mismatch(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_link3"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_symlink()

    test_target_path.unlink()
    test_target_path.symlink_to("/bad-target")
    assert test_target_path.is_symlink()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert file_problems == []
    assert dir_problems == []
    assert len(link_problems) == 1
    assert link_problems[0][0] == test_target
    assert link_problems[0][1].startswith("link destination mismatch")
    assert "/bad-target" in link_problems[0][1]


def test_verify_catalog_file_missing(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_file2"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_file()

    test_target_path.unlink()
    assert not test_target_path.exists()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert dir_problems == []
    assert link_problems == []
    assert file_problems == [(test_target, "file missing")]


def test_verify_catalog_file_not_a_file(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_file3"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_file()

    # is a link to a file
    test_target_path.unlink()
    test_target_path.symlink_to("../test_file1")
    assert test_target_path.is_symlink()
    assert test_target_path.exists(follow_symlinks=True)
    assert test_target_path.is_file()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert dir_problems == []
    assert link_problems == []
    assert file_problems == [(test_target, "not a file")]

    # is a link to a dir
    test_target_path.unlink()
    test_target_path.symlink_to("../test_dir1")
    assert test_target_path.is_symlink()
    assert test_target_path.exists(follow_symlinks=True)
    assert test_target_path.is_dir()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert dir_problems == []
    assert link_problems == []
    assert file_problems == [(test_target, "not a file")]

    # is a dir
    test_target_path.unlink()
    test_target_path.mkdir()
    assert test_target_path.is_dir()
    assert test_target_path.exists(follow_symlinks=True)
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert dir_problems == []
    assert link_problems == []
    assert file_problems == [(test_target, "not a file")]


def test_verify_catalog_file_mode_mismatch(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_file2"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_file()

    test_target_path.chmod(0o000)
    assert test_target_path.is_file()
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert dir_problems == []
    assert link_problems == []
    assert len(file_problems) == 1
    assert file_problems[0][0] == test_target
    assert file_problems[0][1].startswith("file mode mismatch")
    assert "000" in file_problems[0][1]


def test_verify_catalog_file_size_mismatch(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_file2"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_file()

    with open(test_target_path, "wb") as fp:
        fp.write(b"\0" * 128)
    assert test_target_path.is_file()
    assert test_target_path.stat().st_size == 128
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert dir_problems == []
    assert link_problems == []
    assert len(file_problems) == 1
    assert file_problems[0][0] == test_target
    assert file_problems[0][1].startswith("file size mismatch")
    assert "128B" in file_problems[0][1]


def test_verify_catalog_file_digest_mismatch(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir2/test_file2"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_file()

    test_target_size = test_target_path.stat().st_size
    rand_data, rand_digest = gen_random_data_with_digest(catalog_to_verify.digest_algo,
                                                         range(test_target_size, test_target_size + 1))
    with open(test_target_path, "wb") as fp:
        fp.write(rand_data)
    assert test_target_path.is_file()
    assert test_target_path.stat().st_size == test_target_size
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert dir_problems == []
    assert link_problems == []
    assert len(file_problems) == 1
    assert file_problems[0][0] == test_target
    assert file_problems[0][1].startswith(f"file {catalog_to_verify.digest_algo.upper()} mismatch")
    assert rand_digest in file_problems[0][1]


def test_verify_catalog_mixed_failure(setup_test_tree):
    root, expect_data_catalog = setup_test_tree
    catalog_to_verify = FileCatalog(**expect_data_catalog)
    test_target = "test_dir1"
    test_target_path = pathlib.Path(root) / test_target
    assert test_target_path.is_dir()

    test_target_path.rmdir()
    test_target_path.symlink_to("test_dir2/test_file2")
    assert test_target_path.is_symlink()
    assert test_target_path.exists(follow_symlinks=True)
    assert test_target_path.is_file()

    test_target_size = test_target_path.stat().st_size
    rand_data, rand_digest = gen_random_data_with_digest(catalog_to_verify.digest_algo,
                                                         range(test_target_size, test_target_size + 1))
    with open(test_target_path, "wb") as fp:
        fp.write(rand_data)
    assert test_target_path.is_file()
    assert test_target_path.stat().st_size == test_target_size
    file_problems, link_problems, dir_problems = verify_catalog(catalog_to_verify, root)
    assert dir_problems == [("test_dir1", "not a directory")]
    assert len(file_problems) == 1
    assert file_problems[0][0] == "test_dir2/test_file2"
    assert file_problems[0][1].startswith(f"file {catalog_to_verify.digest_algo.upper()} mismatch")
    assert rand_digest in file_problems[0][1]
    for problem in link_problems:
        assert problem[1].startswith('link destination mismatch')
        assert "test_dir1" in problem[1]
        assert "test_dir2/test_file2" in problem[1]
