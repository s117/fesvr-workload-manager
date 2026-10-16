import os
from typing import List, Optional

from fwm.database.catalog_store import save_catalog, load_catalog
from fwm.database.schema import Sysroot
from fwm.repo.errors import AlreadyExistsError, NotFoundError
from fwm.repo.repo import Repo, catalog_of, check_name, copy_source, REPO_PERM_MASK
from fwm.data_loader.file_catalog import FileCatalog
from fwm.utils.file_catalog import (
    verify_catalog, find_absolute_links, catalog_differences, FailedDetail
)
from fwm.utils.fs import set_tree_writable
from fwm.utils.log import Logger


def get_sysroot(name: str) -> Sysroot:
    sysroot = Sysroot.get_or_none(Sysroot.name == name)
    if sysroot is None:
        raise NotFoundError(f"no sysroot named '{name}'")
    return sysroot


def _existing_sysroot(sysroot: Sysroot, catalog: Optional[FileCatalog], src_dir: str,
                      exist_ok: bool) -> Sysroot:
    """
    The answer to importing a sysroot under a name already used: that sysroot with --exist-ok, if the tree is the same,
    else an error.

    :param catalog: The catalog of the tree being imported, if already made.
    """
    what = f"a sysroot named '{sysroot.name}' already exists"
    if not exist_ok:
        raise AlreadyExistsError(what)
    if catalog is None:
        catalog = catalog_of(src_dir, src_dir)
    differences = catalog_differences(catalog, load_catalog(sysroot.file_catalog))
    if differences:
        raise AlreadyExistsError(
            f"{what}, with different content: {len(differences)} path(s) differ, e.g. {differences[0]}")
    return sysroot


def add_sysroot(repo: Repo, src_dir: str, name: str, description: Optional[str] = None,
                exist_ok: bool = False) -> Sysroot:
    """
    Import a sysroot: copy the tree into the repository, catalog it with its original permissions, and make the copy
    read-only.

    :param exist_ok: If a sysroot of this name with the same content exists, return it instead of failing.
    :raise AlreadyExistsError: A sysroot of this name exists.
    """
    check_name("sysroot", name)
    if not os.path.isdir(src_dir):
        raise NotFoundError(f"{src_dir} is not a directory")
    existing = Sysroot.get_or_none(Sysroot.name == name)
    if existing is not None:
        return _existing_sysroot(existing, None, src_dir, exist_ok)

    with repo.staging() as staged:
        copy_source(src_dir, staged)
        catalog = catalog_of(staged, src_dir)

        abs_links = find_absolute_links(catalog)
        if abs_links:
            Logger.get_logger().warning(
                f"sysroot '{name}' has {len(abs_links)} absolute symlink(s). FESVR's +chroot only prefixes path "
                f"strings, so in a recording run the host resolves them from the host's root, not the sysroot's:\n" +
                "\n".join(f"  {link.path} -> {link.dest}" for link in abs_links))

        set_tree_writable(staged)  # to move it to another parent
        with repo.importing() as place:
            # another import of this name may have committed meanwhile
            existing = Sysroot.get_or_none(Sysroot.name == name)
            if existing is not None:
                return _existing_sysroot(existing, catalog, src_dir, exist_ok)
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
