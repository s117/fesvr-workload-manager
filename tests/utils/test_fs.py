import pathlib
import shutil

import pytest


def test_sha256(fs):
    from fwm.utils.fs import sha256_file
    path = pathlib.Path("/test_sha256")
    testfile = (path / "testfile.txt")
    with pytest.raises(FileNotFoundError):
        sha256_file(str(testfile), False)

    path.mkdir()
    testfile.touch()
    assert sha256_file(str(testfile), use_cache=False) == \
           "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    with open(str(testfile), "wb") as f:
        f.write(b"sample")
    assert sha256_file(str(testfile), use_cache=True) == \
           "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_file(str(testfile), use_cache=False) == \
           "af2bdbe1aa9b6ec1e2ade1d694f41fc71a831d0268e9891562113d8a62add1bf"
    assert sha256_file(str(testfile), use_cache=True) == \
           "af2bdbe1aa9b6ec1e2ade1d694f41fc71a831d0268e9891562113d8a62add1bf"

    testfile.unlink()
    assert sha256_file(str(testfile), use_cache=True) == \
           "af2bdbe1aa9b6ec1e2ade1d694f41fc71a831d0268e9891562113d8a62add1bf"
    with pytest.raises(FileNotFoundError):
        sha256_file(str(testfile), False)
    assert sha256_file(str(testfile), use_cache=True) == \
           "af2bdbe1aa9b6ec1e2ade1d694f41fc71a831d0268e9891562113d8a62add1bf"

    shutil.rmtree(str(path))


def test_is_valid_sha256(fs):
    from fwm.utils.fs import is_valid_sha256
    assert not is_valid_sha256("")
    assert not is_valid_sha256("1")
    assert is_valid_sha256("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    assert not is_valid_sha256("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    assert not is_valid_sha256("Pe3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85")


def test_remove_path(fs):
    from fwm.utils.fs import remove_path

    path = pathlib.Path("/test_file")
    assert not path.exists()

    path.touch()
    assert path.is_file()

    remove_path(str(path))
    assert not path.exists()

    path = pathlib.Path("/test_dir")
    file_to_remove = path / "file_to_remove"
    assert not path.exists()
    assert not file_to_remove.exists()

    path.mkdir()
    assert path.is_dir()
    assert not file_to_remove.exists()

    file_to_remove.touch()
    assert file_to_remove.is_file()
    assert file_to_remove.exists()

    remove_path(str(path))
    assert not file_to_remove.exists()
    assert not path.exists()


def test_human_readable_size():
    from fwm.utils.fs import human_readable_size
    assert human_readable_size(1000) == "%d %s" % (1000, "B")
    assert human_readable_size(2000) == "%.1f %s" % (2000 / 1024, "K")
    assert human_readable_size(3000 * 1024 ** 1) == "%.1f %s" % (3000 / 1024, "M")
    assert human_readable_size(4000 * 1024 ** 2) == "%.1f %s" % (4000 / 1024, "G")
    assert human_readable_size(5000 * 1024 ** 3) == "%.1f %s" % (5000 / 1024, "T")


def test_get_path_size(fs):
    from fwm.utils.fs import get_path_size
    path = pathlib.Path("/test_get_path_size")
    with pytest.raises(FileNotFoundError):
        get_path_size(str(path))

    file1 = path / "file1"
    dir1 = path / "dir1"
    file2 = dir1 / "file2"
    dir2 = dir1 / "dir2"
    path.mkdir()
    file1.touch()
    dir1.mkdir()
    file2.touch()
    dir2.mkdir()
    assert get_path_size(str(path)) == 0
    assert get_path_size(str(file1)) == 0
    assert get_path_size(str(dir1)) == 0
    assert get_path_size(str(file2)) == 0
    assert get_path_size(str(dir2)) == 0

    file1_size = 11
    with open(file1, "wb") as f1:
        f1.write(b"B" * file1_size)
    assert get_path_size(str(file1)) == file1_size
    assert get_path_size(str(path)) == file1_size

    file2_size = 1024 * 1024 * 1024  # 1GB
    with open(file2, "wb") as f2:
        f2.write(b"B" * file2_size)
    assert get_path_size(str(file2)) == file2_size
    assert get_path_size(str(path)) == file1_size + file2_size
