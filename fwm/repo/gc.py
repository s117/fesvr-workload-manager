"""
Leftovers of interrupted imports and removals, and their removal (fwm repo gc).

Imports stage their content in the staging area, then rename it into place in the transaction that adds its rows, and
removals delete their files after committing (see Repo.importing() and Repo.removal()). A process killed at the wrong
moment can therefore leave:
- a staging entry whose import died;
- a file or tree in the repository that no row refers to.
Neither is ever a row whose files are missing.
"""
import fcntl
import os
import stat
import time
from dataclasses import dataclass
from typing import Dict, Iterator, List, Set

from fwm.database.schema import Sysroot, DecoupledTrace, DecoupledCheckpoint
from fwm.repo.repo import Repo, STAGING_DIR, STAGING_LOCK_SUFFIX, SYSROOTS_DIR, TRACES_DIR, CHECKPOINTS_DIR

# A staging entry younger than this is never a leftover: its import may be starting (a lock file is created, then
# locked), and a clock skew between hosts sharing the repository won't make a live one look old.
MIN_AGE_SECONDS = 600


@dataclass
class Leftover:
    path: str
    reason: str


def _age(path: str) -> float:
    return time.time() - os.lstat(path).st_mtime


def _owner_is_dead(lock: str) -> bool:
    """
    Whether no process holds the lock of a staging entry. A shared lock tells it as well as an exclusive one, and needs
    only read access, so that verify works in a repository it can't write to (on NFS, flock is emulated with fcntl
    locks, where an exclusive one needs a file opened for writing).
    """
    try:
        fd = os.open(lock, os.O_RDONLY)
    except FileNotFoundError:
        return False  # its import just ended
    except PermissionError:
        return False  # can't tell
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


def _staging_leftovers(repo: Repo, min_age: float) -> Iterator[Leftover]:
    staging = os.path.join(repo.path, STAGING_DIR)
    entries: Dict[str, List[str]] = {}
    for name in os.listdir(staging):
        stem = name[:-len(STAGING_LOCK_SUFFIX)] if name.endswith(STAGING_LOCK_SUFFIX) else name.split(".", 1)[0]
        entries.setdefault(stem, []).append(name)
    for stem, names in sorted(entries.items()):
        paths = [os.path.join(staging, name) for name in sorted(names)]
        lock = os.path.join(staging, stem + STAGING_LOCK_SUFFIX)
        try:
            if min(_age(p) for p in paths) < min_age:
                continue
        except FileNotFoundError:
            continue  # its import just ended
        if lock not in paths:
            reason = "staging without an owner"
        elif _owner_is_dead(lock):
            reason = "staging of an import that was interrupted"
        else:
            continue
        for p in paths:
            yield Leftover(p, reason)


def _untracked(repo: Repo) -> Iterator[Leftover]:
    sysroots: Set[str] = {s.name for s in Sysroot.select(Sysroot.name)}
    for name in sorted(os.listdir(os.path.join(repo.path, SYSROOTS_DIR))):
        if name not in sysroots:
            yield Leftover(repo.sysroot_dir(name), "no sysroot of this name")

    traces: Set[str] = {str(t.uuid) for t in DecoupledTrace.select(DecoupledTrace.uuid)}
    for name in sorted(os.listdir(os.path.join(repo.path, TRACES_DIR))):
        if name not in traces:
            yield Leftover(os.path.join(repo.path, TRACES_DIR, name), "no trace of this UUID")

    checkpoints: Dict[str, Set[str]] = {}
    for ckpt in DecoupledCheckpoint.select(DecoupledCheckpoint.name, DecoupledTrace.uuid).join(DecoupledTrace):
        checkpoints.setdefault(str(ckpt.trace.uuid), set()).add(ckpt.name)
    for name in sorted(os.listdir(os.path.join(repo.path, CHECKPOINTS_DIR))):
        path = os.path.join(repo.path, CHECKPOINTS_DIR, name)
        if name not in traces:
            yield Leftover(path, "no trace of this UUID")
            continue
        files = sorted(os.listdir(path))
        if not files and name not in checkpoints:
            yield Leftover(path, "an empty checkpoint directory")
        for file in files:
            if file not in checkpoints.get(name, set()):
                yield Leftover(os.path.join(path, file), "no checkpoint of this name")


def find_leftovers(repo: Repo, min_age: float = MIN_AGE_SECONDS) -> List[Leftover]:
    """
    The leftovers in the repository. Without the write lock (see collect_garbage()), an import that is just between
    placing its content and committing may be reported too.
    """
    return list(_staging_leftovers(repo, min_age)) + list(_untracked(repo))


def collect_garbage(repo: Repo, min_age: float = MIN_AGE_SECONDS) -> List[Leftover]:
    """
    Remove the leftovers in the repository.

    They are found while the write lock is held, so that no import is between placing its content and committing,
    and moved into a staging area of this collection. They are deleted after the lock is released, so that deleting a
    large tree doesn't hold up other commands.

    :return: The leftovers removed.
    """
    with repo.staging() as trash:
        os.mkdir(trash)
        with repo.db.atomic():
            leftovers = find_leftovers(repo, min_age)
            for i, leftover in enumerate(leftovers):
                # a directory must be writable to be moved to another parent
                mode = os.lstat(leftover.path).st_mode
                if stat.S_ISDIR(mode):
                    os.chmod(leftover.path, stat.S_IMODE(mode) | stat.S_IRWXU)
                os.rename(leftover.path, os.path.join(trash, str(i)))
    return leftovers
