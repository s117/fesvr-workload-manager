import json
import os
import pathlib
from typing import Any, Dict

import pytest

from fwm.repo.repo import Repo
from fwm.utils.fs import set_tree_writable
from tests.fesvr_files import DEFAULT_BOOT_ELF


@pytest.fixture(autouse=True)
def _writable_tmp_path(request):
    """Tests leave read-only trees (like a repository's), which pytest couldn't remove: make them writable again."""
    yield
    tmp_path = getattr(request.node, "funcargs", {}).get("tmp_path")
    if tmp_path is not None and tmp_path.exists():
        set_tree_writable(str(tmp_path))


@pytest.fixture
def repo(tmp_path):
    r = Repo.init(str(tmp_path / "repo"))
    yield r
    r.close()


@pytest.fixture
def sysroot_src(tmp_path) -> pathlib.Path:
    """A small sysroot: pk at /boot/pk, a program and its input in /app."""
    root = tmp_path / "src" / "sr"
    (root / "boot").mkdir(parents=True)
    (root / "app" / "data").mkdir(parents=True)
    (root / "boot" / "pk").write_bytes(DEFAULT_BOOT_ELF)
    (root / "app" / "prog.rv64").write_bytes(b"\x7fELF program")
    (root / "app" / "input.txt").write_text("1\n2\n")
    (root / "app" / "data" / "private").write_text("secret")
    (root / "app" / "pk-link").symlink_to("../boot/pk")
    (root / "app" / "abs-link").symlink_to("/boot/pk")
    (root / "app" / "prog.rv64").chmod(0o750)
    (root / "app" / "data" / "private").chmod(0o600)
    (root / "app" / "data").chmod(0o700)
    return root


def entry_point(**overrides: Any) -> Dict[str, Any]:
    entry = {
        "command": "prog.rv64 input.txt",
        "working_directory": "/app",
        "envs": None,
        "stdin_file": None,
        "boot_elf": "/boot/pk",
        "mem_mb": 1,
        "nprocs": 1,
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def write_entry_point(tmp_path):
    counter = [0]

    def write(**overrides: Any) -> str:
        counter[0] += 1
        path = tmp_path / f"entry_point{counter[0]}.json"
        path.write_text(json.dumps(entry_point(**overrides)))
        return str(path)

    return write


def mode(path) -> int:
    return os.lstat(path).st_mode & 0o777
