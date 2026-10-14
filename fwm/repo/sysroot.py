import os
from typing import List, Optional

from fwm.database.catalog_store import save_catalog, load_catalog
from fwm.database.schema import Sysroot
from fwm.repo.errors import FwmValidationError, NotFoundError
from fwm.repo.repo import Repo, check_name, DIGEST_ALGO, REPO_PERM_MASK
from fwm.utils.file_catalog import create_catalog, verify_catalog, find_absolute_links, FailedDetail
from fwm.utils.fs import copy_tree, set_tree_writable, remove_tree
from fwm.utils.log import Logger


def get_sysroot(name: str) -> Sysroot:
    sysroot = Sysroot.get_or_none(Sysroot.name == name)
    if sysroot is None:
        raise NotFoundError(f"no sysroot named '{name}'")
    return sysroot


def add_sysroot(repo: Repo, src_dir: str, name: str, description: Optional[str] = None) -> Sysroot:
    """
    Import a sysroot: copy the tree into the repository, catalog it with its original permissions, and make the copy
    read-only.
    """
    check_name("sysroot", name)
    if not os.path.isdir(src_dir):
        raise NotFoundError(f"{src_dir} is not a directory")
    if Sysroot.get_or_none(Sysroot.name == name) is not None:
        raise FwmValidationError(f"a sysroot named '{name}' already exists")

    with repo.staging() as staged:
        copy_tree(src_dir, staged)
        try:
            catalog = create_catalog(DIGEST_ALGO, staged)
        except NotImplementedError as e:
            raise FwmValidationError(str(e).replace(staged, src_dir))

        abs_links = find_absolute_links(catalog)
        if abs_links:
            Logger.get_logger().warning(
                f"sysroot '{name}' has {len(abs_links)} absolute symlink(s). FESVR's +chroot only prefixes path "
                f"strings, so in a recording run the host resolves them from the host's root, not the sysroot's:\n" +
                "\n".join(f"  {link.path} -> {link.dest}" for link in abs_links))

        set_tree_writable(staged)  # to move it to another parent
        with repo.importing() as place:
            if Sysroot.get_or_none(Sysroot.name == name) is not None:
                raise FwmValidationError(f"a sysroot named '{name}' already exists")
            sysroot = Sysroot.create(name=name, description=description, file_catalog=save_catalog(catalog))
            place(staged, repo.sysroot_dir(name))
    return sysroot


def verify_sysroot(repo: Repo, sysroot: Sysroot) -> List[FailedDetail]:
    """
    Verify a sysroot in the repository against its catalog, ignoring the write bits it lost on import.
    """
    file_problems, link_problems, dir_problems = verify_catalog(
        load_catalog(sysroot.file_catalog), repo.sysroot_dir(sysroot.name), perm_mask=REPO_PERM_MASK)
    return sorted(file_problems + link_problems + dir_problems)


def spawn_sysroot(repo: Repo, name: str, dest: str, force: bool = False) -> None:
    """
    Make a working copy of a sysroot for a recording run: copy it, give it back the permissions of its catalog, and
    verify it.

    :param force: Replace dest if it exists.
    """
    sysroot = get_sysroot(name)
    dest = os.path.abspath(dest)
    if os.path.lexists(dest):
        if not force:
            raise FwmValidationError(f"{dest} already exists")
        remove_tree(dest)

    copy_tree(repo.sysroot_dir(name), dest)
    catalog = load_catalog(sysroot.file_catalog)

    # the root itself is not in the catalog
    os.chmod(dest, 0o755)
    # files first, then directories deepest first, so that no directory loses its search permission too early
    for file in catalog.files:
        os.chmod(os.path.join(dest, file.path), file.perm)
    for d in sorted(catalog.dirs, key=lambda d: d.path.count("/"), reverse=True):
        os.chmod(os.path.join(dest, d.path), d.perm)

    file_problems, link_problems, dir_problems = verify_catalog(catalog, dest)
    problems = sorted(file_problems + link_problems + dir_problems)
    if problems:
        raise FwmValidationError(
            f"the copy of sysroot '{name}' at {dest} doesn't match its catalog:\n" +
            "\n".join(f"  {path}: {reason}" for path, reason in problems))
