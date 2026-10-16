import os

import pytest

from fwm.database.schema import Sysroot
from fwm.repo import query
from fwm.repo.entry import add_entry, get_entry
from fwm.repo.errors import AlreadyExistsError, FwmValidationError, NotFoundError
from fwm.repo.spawn import spawn, SpawnMode
from fwm.repo.sysroot import add_sysroot, verify_sysroot
from tests.conftest import mode


@pytest.fixture
def entries(repo, sysroot_src, write_entry_point):
    """Entry point "copied" (copy_spawn true, the default) and "linked" (copy_spawn false), of sysroot "sr"."""
    add_sysroot(repo, str(sysroot_src), "sr")
    add_entry(repo, "sr", "wl", "copied", write_entry_point())
    add_entry(repo, "sr", "wl", "linked", write_entry_point(copy_spawn=False))


def stored(repo, *path):
    return os.path.join(os.path.abspath(repo.sysroot_dir("sr")), *path)


def test_copy(repo, entries, tmp_path):
    dest = tmp_path / "run" / "sysroot"
    dest.parent.mkdir()
    assert spawn(repo, "copied", str(dest)) == SpawnMode.COPY
    # real files, with the catalog's permissions back
    assert not (dest / "app" / "prog.rv64").is_symlink()
    assert mode(dest / "app" / "prog.rv64") == 0o750
    assert mode(dest / "app" / "data") == 0o700
    assert os.readlink(dest / "app" / "pk-link") == "../boot/pk"
    (dest / "app" / "input.txt").write_text("changed by the program")
    (dest / "app" / "output.txt").write_text("written by the program")

    with pytest.raises(FwmValidationError, match="already exists"):
        spawn(repo, "copied", str(dest))
    spawn(repo, "copied", str(dest), force=True)
    assert not (dest / "app" / "output.txt").exists()
    assert (dest / "app" / "input.txt").read_text() == "1\n2\n"


def test_link(repo, entries, sysroot_src, tmp_path):
    dest = tmp_path / "sysroot"
    stored_modes = {p: mode(stored(repo, p)) for p in ("app/prog.rv64", "app/input.txt", "boot/pk")}
    assert spawn(repo, "linked", str(dest)) == SpawnMode.LINK

    # files: links to the repository's files
    assert os.readlink(dest / "app" / "input.txt") == stored(repo, "app", "input.txt")
    assert os.readlink(dest / "boot" / "pk") == stored(repo, "boot", "pk")
    assert (dest / "app" / "input.txt").read_text() == "1\n2\n"
    # the sysroot's own links, as they are
    assert os.readlink(dest / "app" / "pk-link") == "../boot/pk"
    assert os.readlink(dest / "app" / "abs-link") == "/boot/pk"
    # directories: real, with the catalog's permissions
    assert not (dest / "app").is_symlink()
    assert mode(dest / "app" / "data") == 0o700
    assert mode(dest / "app") == mode(sysroot_src / "app")
    # the repository's files keep their (read-only) permissions
    assert {p: mode(stored(repo, p)) for p in stored_modes} == stored_modes

    # the program can create files, but not write to the ones it finds
    (dest / "app" / "output.txt").write_text("written by the program")
    with pytest.raises(PermissionError):
        open(dest / "app" / "input.txt", "r+")
    with pytest.raises(PermissionError):
        open(dest / "app" / "input.txt", "w")
    # it can delete or replace them: only the link goes
    (dest / "app" / "input.txt").unlink()
    (dest / "app" / "pk-link").unlink()

    spawn(repo, "linked", str(dest), force=True)
    assert not (dest / "app" / "output.txt").exists()
    assert verify_sysroot(repo, repo_sysroot(repo)) == []


def repo_sysroot(repo):
    return Sysroot.get(Sysroot.name == "sr")


def test_mode_overrides_copy_spawn(repo, entries, tmp_path):
    assert spawn(repo, "copied", str(tmp_path / "a"), mode=SpawnMode.LINK) == SpawnMode.LINK
    assert (tmp_path / "a" / "app" / "input.txt").is_symlink()
    assert spawn(repo, "linked", str(tmp_path / "b"), mode=SpawnMode.COPY) == SpawnMode.COPY
    assert not (tmp_path / "b" / "app" / "input.txt").is_symlink()


def test_force_over_a_linked_tree(repo, entries, tmp_path):
    """Replacing a linked spawn removes the links, never what they point to; a copy may replace it too."""
    dest = tmp_path / "sysroot"
    spawn(repo, "linked", str(dest))
    os.chmod(dest / "app", 0o555)  # a directory the program made read-only
    spawn(repo, "copied", str(dest), force=True)
    assert not (dest / "app" / "input.txt").is_symlink()
    assert verify_sysroot(repo, repo_sysroot(repo)) == []


@pytest.mark.parametrize("entry", ["copied", "linked"])
def test_damaged_sysroot(repo, entries, tmp_path, entry):
    """A copy is checked file by file; a linked spawn only checks the tree (`fwm repo verify` checks the content)."""
    path = stored(repo, "app", "input.txt")
    os.chmod(path, 0o644)
    with open(path, "w") as f:
        f.write("damaged\n")
    if entry == "copied":
        with pytest.raises(FwmValidationError, match=r"doesn't match its catalog:\n  app/input.txt: "):
            spawn(repo, entry, str(tmp_path / "s"))
    else:
        spawn(repo, entry, str(tmp_path / "s"))
        os.chmod(os.path.dirname(path), 0o755)
        os.remove(path)
        with pytest.raises(FwmValidationError, match=r"link of sysroot 'sr' .* doesn't match its catalog:\n"
                                                     r"  app/input.txt: the repository's file .* is missing"):
            spawn(repo, entry, str(tmp_path / "s"), force=True)


def test_missing_entry(repo, tmp_path):
    with pytest.raises(NotFoundError):
        spawn(repo, "nope", str(tmp_path / "x"))


def test_refuses_repository(repo, entries):
    for dest in (repo.sysroot_dir("sr"), os.path.join(repo.path, "elsewhere")):
        with pytest.raises(FwmValidationError, match="inside the repository"):
            spawn(repo, "linked", dest, force=True)
    assert os.path.isfile(stored(repo, "boot", "pk"))


def test_copy_spawn_is_a_launch_field(repo, entries, write_entry_point):
    """Adding the same entry point again with another copy_spawn is adding a different one."""
    assert add_entry(repo, "sr", "wl", "linked", write_entry_point(copy_spawn=False), exist_ok=True).copy_spawn is False
    with pytest.raises(AlreadyExistsError, match="differs"):
        add_entry(repo, "sr", "wl", "linked", write_entry_point(), exist_ok=True)
    # shown with the entry point (fwm show, list --json, run.json)
    assert query.entry_view(get_entry("linked"))["copy_spawn"] is False
    assert query.entry_view(get_entry("copied"))["copy_spawn"] is True
