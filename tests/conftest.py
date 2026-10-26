import json
import os
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Sequence

import pytest

from fwm.data_loader.checkpoint import read_checkpoint_header
from fwm.database.schema import DecoupledCheckpoint
from fwm.repo.checkpoint import add_checkpoints
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


def import_checkpoints(repo: Repo, trace_ref: str, files: Sequence[str], simpoint_info: Optional[Dict] = None,
                       **kwargs: Any) -> List[DecoupledCheckpoint]:
    """
    Import checkpoint files as `fwm repo compose add checkpoint` takes them: copied into a new checkpoint directory, with a
    description file requesting each one where it was taken. A checkpoint is named after its file.

    :param simpoint_info: The content of the SimPoint information file, if any.
    """
    work = tempfile.mkdtemp(prefix="import-", dir=os.path.dirname(os.path.abspath(files[0])))
    checkpoint_dir = os.path.join(work, "checkpoints")
    os.mkdir(checkpoint_dir)
    desc = os.path.join(work, "ckpt.desc")
    with open(desc, "w") as f:
        for file in files:
            shutil.copyfile(file, os.path.join(checkpoint_dir, os.path.basename(file)))
            f.write(f"{os.path.basename(file)}: {read_checkpoint_header(str(file)).harts[0].csr_count}\n")
    info = None
    if simpoint_info is not None:
        info = os.path.join(work, "simpoints.json")
        with open(info, "w") as f:
            json.dump(simpoint_info, f)
    return add_checkpoints(repo, trace_ref, desc, checkpoint_dir, simpoint_info=info, **kwargs)


def import_checkpoint(repo: Repo, trace_ref: str, file: str, **kwargs: Any) -> DecoupledCheckpoint:
    """Import one checkpoint file; see import_checkpoints()."""
    return import_checkpoints(repo, trace_ref, [file], **kwargs)[0]
