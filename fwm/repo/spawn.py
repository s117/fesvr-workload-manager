"""
Spawning: a working copy of an entry point's sysroot, in which its program runs its real system calls (a record run,
or `fwm mkgen from-sysroot`).

A sysroot is spawned as a copy, or as links:
- a copy has every file and directory of the sysroot, with the permissions of its catalog, so the program can change
  anything in it;
- linked, it has real directories (with their catalog permissions, so the program can create, rename and delete
  files in them), but each file is a symbolic link to the repository's read-only file. It takes no time and no space,
  even for a large sysroot, but the program can't open an existing file for writing, stat() reports it read-only,
  and lstat() and readlink() see a link to the repository. Nothing the program does changes the repository: FESVR
  proxies no chmod-like system call.

The entry point's copy_spawn chooses (like riscv-simenv's): an entry point whose program only reads the files it finds
can be linked.
"""
import enum
import os
from typing import List

from fwm.data_loader.file_catalog import FileCatalog
from fwm.database.catalog_store import load_catalog
from fwm.database.schema import NativeEntry
from fwm.repo.lookup import get_entry
from fwm.repo.errors import FwmValidationError
from fwm.repo.repo import Repo
from fwm.utils.file_catalog import verify_catalog, FailedDetail
from fwm.utils.fs import copy_tree, remove_tree


class SpawnMode(str, enum.Enum):
    AUTO = "auto"  # as the entry point's copy_spawn says
    COPY = "copy"
    LINK = "link"


def spawn_mode(entry: NativeEntry, mode: SpawnMode) -> SpawnMode:
    """The way an entry point's sysroot is spawned: COPY or LINK."""
    if mode == SpawnMode.AUTO:
        return SpawnMode.COPY if entry.copy_spawn else SpawnMode.LINK
    return mode


def _set_dir_perms(catalog: FileCatalog, dest: str) -> None:
    # the root itself is not in the catalog; deepest first, so that no directory loses its search permission too early
    os.chmod(dest, 0o755)
    for d in sorted(catalog.dirs, key=lambda d: d.path.count("/"), reverse=True):
        os.chmod(os.path.join(dest, d.path), d.perm)


def _copy(src: str, catalog: FileCatalog, dest: str) -> List[FailedDetail]:
    copy_tree(src, dest)
    for file in catalog.files:
        os.chmod(os.path.join(dest, file.path), file.perm)
    _set_dir_perms(catalog, dest)

    file_problems, link_problems, dir_problems = verify_catalog(catalog, dest)
    return file_problems + link_problems + dir_problems


def _link(src: str, catalog: FileCatalog, dest: str) -> List[FailedDetail]:
    os.mkdir(dest)
    for d in sorted(catalog.dirs, key=lambda d: d.path.count("/")):
        os.mkdir(os.path.join(dest, d.path))
    for file in catalog.files:
        os.symlink(os.path.join(src, file.path), os.path.join(dest, file.path))
    for link in catalog.links:
        os.symlink(link.dest, os.path.join(dest, link.path))
    # only directories get permissions: chmod() of a file would follow its link into the repository
    _set_dir_perms(catalog, dest)

    # what the links point to is the repository's content, which `fwm repo verify` checks; here, only the tree
    problems: List[FailedDetail] = []
    for file in catalog.files:
        path = os.path.join(dest, file.path)
        target = os.path.join(src, file.path)
        if not os.path.islink(path) or os.readlink(path) != target:
            problems.append((file.path, f"not a link to {target}"))
        elif not os.path.isfile(path):
            problems.append((file.path, f"the repository's file {target} is missing"))
    for link in catalog.links:
        path = os.path.join(dest, link.path)
        if not os.path.islink(path) or os.readlink(path) != link.dest:
            problems.append((link.path, f"not a link to {link.dest}"))
    _, _, dir_problems = verify_catalog(FileCatalog(creation_time=catalog.creation_time, digest_algo=catalog.digest_algo,
                                                    files=[], links=[], dirs=catalog.dirs), dest)
    return problems + dir_problems


def spawn(repo: Repo, entry_name: str, dest: str, force: bool = False, mode: SpawnMode = SpawnMode.AUTO) -> SpawnMode:
    """
    Make a working copy of an entry point's sysroot at dest, as a copy or as links (see the module's description), and
    check it against the sysroot's catalog.

    :param mode: AUTO follows the entry point's copy_spawn; COPY or LINK forces one.
    :param force: Replace dest if it exists.
    :return: How it was spawned: COPY or LINK.
    """
    entry = get_entry(entry_name)
    sysroot = entry.sysroot
    dest = os.path.abspath(dest)
    repo_path = os.path.realpath(repo.path)
    if os.path.commonpath([os.path.realpath(dest), repo_path]) == repo_path:
        raise FwmValidationError(f"{dest} is inside the repository: spawn the sysroot somewhere else")
    if os.path.lexists(dest):
        if not force:
            raise FwmValidationError(f"{dest} already exists")
        remove_tree(dest)

    how = spawn_mode(entry, mode)
    src = os.path.abspath(repo.sysroot_dir(sysroot.name))
    catalog = load_catalog(sysroot.file_catalog)
    problems = (_copy if how == SpawnMode.COPY else _link)(src, catalog, dest)
    if problems:
        raise FwmValidationError(
            f"the {how.value} of sysroot '{sysroot.name}' at {dest} doesn't match its catalog:\n" +
            "\n".join(f"  {path}: {reason}" for path, reason in sorted(problems)))
    return how
