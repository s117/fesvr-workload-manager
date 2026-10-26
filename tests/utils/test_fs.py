import os
import pathlib
import subprocess
import sys

import pytest


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


def make_tree(root: pathlib.Path) -> None:
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "file").write_text("data")
    (root / "top").write_text("top")
    (root / "link").symlink_to("/abs/target")
    (root / "sub" / "file").chmod(0o640)
    (root / "sub").chmod(0o550)
    root.chmod(0o555)


def test_copy_tree(tmp_path):
    from fwm.utils.fs import copy_tree

    src = tmp_path / "src"
    make_tree(src)
    dst = tmp_path / "dst"
    copy_tree(str(src), str(dst))
    assert (dst / "sub" / "file").read_text() == "data"
    assert (dst / "sub" / "file").stat().st_mode & 0o777 == 0o640
    assert (dst / "sub").stat().st_mode & 0o777 == 0o550
    assert dst.stat().st_mode & 0o777 == 0o555
    assert (dst / "link").is_symlink()
    assert str((dst / "link").readlink()) == "/abs/target"

    with pytest.raises(FileExistsError):
        copy_tree(str(src), str(dst))


@pytest.mark.parametrize("with_cp", [True, False])
def test_copy_tree_file(tmp_path, monkeypatch, with_cp):
    import shutil
    from fwm.utils.fs import copy_tree

    if not with_cp:
        monkeypatch.setattr(shutil, "which", lambda name: None)
    src = tmp_path / "file"
    src.write_text("data")
    src.chmod(0o440)
    copy_tree(str(src), str(tmp_path / "copy"))
    assert (tmp_path / "copy").read_text() == "data"
    assert (tmp_path / "copy").stat().st_mode & 0o777 == 0o440


def test_set_tree_readonly_and_writable(tmp_path):
    from fwm.utils.fs import set_tree_readonly, set_tree_writable

    root = tmp_path / "root"
    make_tree(root)
    root.chmod(0o755)
    (root / "sub").chmod(0o750)
    set_tree_readonly(str(root))
    assert (root / "sub" / "file").stat().st_mode & 0o777 == 0o440
    assert (root / "top").stat().st_mode & 0o777 == 0o444
    assert (root / "sub").stat().st_mode & 0o777 == 0o550
    assert root.stat().st_mode & 0o777 == 0o555

    set_tree_writable(str(root))
    assert (root / "sub").stat().st_mode & 0o777 == 0o750
    assert root.stat().st_mode & 0o777 == 0o755
    assert (root / "sub" / "file").stat().st_mode & 0o777 == 0o440  # files are left as they are


def test_set_file_readonly(tmp_path):
    from fwm.utils.fs import set_tree_readonly

    path = tmp_path / "file"
    path.write_text("x")
    path.chmod(0o664)
    set_tree_readonly(str(path))
    assert path.stat().st_mode & 0o777 == 0o444


def test_remove_tree_readonly(tmp_path):
    from fwm.utils.fs import remove_tree, set_tree_readonly

    root = tmp_path / "root"
    make_tree(root)
    set_tree_readonly(str(root))
    remove_tree(str(root))
    assert not root.exists()
    remove_tree(str(root))  # a missing path is ignored


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="GNU cp")
def test_copy_tree_cp_killed(tmp_path):
    """A cp killed by a signal prints nothing: the error says how it ended (here, beyond a file size limit)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "big").write_bytes(b"x" * (2 << 20))
    script = (f"import resource; resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20)); "
              f"from fwm.utils.fs import copy_tree; copy_tree({str(tmp_path / 'src')!r}, {str(tmp_path / 'dst')!r})")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    if result.returncode == 0:
        pytest.skip("the file size limit didn't stop cp here (e.g. it cloned the file)")
    assert "cp was killed by signal 25" in result.stderr  # SIGXFSZ


def _other_group():
    """A group of the user other than their primary one, or None."""
    return next((g for g in os.getgroups() if g != os.getgid()), None)


@pytest.mark.skipif(_other_group() is None, reason="the user has a single group")
@pytest.mark.parametrize("with_cp", [True, False])
def test_copy_tree_takes_the_group_of_where_it_goes(tmp_path, monkeypatch, with_cp):
    """In a setgid directory (a group-shared repository), a copy takes the directory's group, not the original's."""
    import shutil
    from fwm.utils.fs import copy_tree

    if not with_cp:
        monkeypatch.setattr(shutil, "which", lambda name: None)
    group = _other_group()
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chown(shared, -1, group)
    shared.chmod(0o2770)
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "file").write_text("data")
    (src / "sub" / "file").chmod(0o640)
    copy_tree(str(src), str(shared / "copy"))
    for path in (shared / "copy", shared / "copy" / "sub", shared / "copy" / "sub" / "file"):
        assert path.stat().st_gid == group, path
    assert (shared / "copy" / "sub" / "file").stat().st_mode & 0o777 == 0o640
