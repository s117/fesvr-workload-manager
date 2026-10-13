import os
import pathlib
from datetime import datetime
from typing import Union, Dict, Tuple, List, Optional

from fwm.data_loader.file_catalog import FileCatalog, FileItem, LinkItem, DirItem
from fwm.utils.digest import Digest


def create_catalog(digest_algo: str, tree_root: str) -> FileCatalog:
    root = pathlib.Path(tree_root).resolve()

    if not root.exists():
        raise FileNotFoundError(tree_root)

    if not root.is_dir():
        raise NotADirectoryError(tree_root)

    files: List[Dict[str, Union[str, int]]] = []
    links: List[Dict[str, Union[str, int]]] = []
    dirs: List[Dict[str, Union[str, int]]] = []

    def handle_link(link_full):
        # Record the link's own text, not where the host resolves it: inside a sysroot an absolute link refers to the
        # target root, and following it (or a chain of links) on the host would make the catalog host-dependent.
        links.append({
            "path": str(link_full.relative_to(root)),
            "perm": link_full.stat(follow_symlinks=False).st_mode & 0o777,
            "dest": os.readlink(link_full),
        })

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        curr_dir = pathlib.Path(dirpath).resolve()

        filenames.sort()
        dirnames.sort()

        for f in filenames:
            f_full = curr_dir / f

            if f_full.is_symlink():
                handle_link(f_full)
            elif f_full.is_file():
                file_stat = f_full.stat()
                files.append({
                    "path": str(f_full.relative_to(root)),
                    "perm": file_stat.st_mode & 0o777,
                    "actual_size": file_stat.st_size,
                    "digest": Digest.digest_file(digest_algo, str(f_full))
                })
            else:
                raise NotImplementedError(
                    f"{f_full} cannot be included by the catalog because it is neither a file nor symlink.")

        for d in dirnames:
            d_full = curr_dir / d

            if d_full.is_symlink():
                handle_link(d_full)
            else:
                dirs.append({
                    "path": str(d_full.relative_to(root)),
                    "perm": d_full.stat().st_mode & 0o777,
                })

    catalog = FileCatalog(
        creation_time=datetime.now().astimezone().isoformat(),  # type: ignore
        digest_algo=digest_algo,
        files=files,  # type: ignore
        links=links,  # type: ignore
        dirs=dirs,  # type: ignore
    )
    return catalog


FailedDetail = Tuple[str, str]  # path, reason
FailedList = List[FailedDetail]


def verify_catalog(catalog: FileCatalog, tree_root: str, use_digest_cache=False) -> \
        Tuple[FailedList, FailedList, FailedList]:
    catalog_root = pathlib.Path(tree_root).resolve()
    digest_algo = catalog.digest_algo

    def validate_file(file_item: FileItem) -> Optional[FailedDetail]:
        file_path = catalog_root / file_item.path
        if not file_path.exists():
            return file_item.path, "file missing"
        if file_path.is_symlink() or not file_path.is_file():
            return file_item.path, "not a file"

        file_stat = file_path.stat()
        file_mode = file_stat.st_mode & 0o777
        if not file_item.perm == file_mode:
            return file_item.path, f"file mode mismatch (expect={file_item.perm:03o}, actual={file_mode:03o})"
        if not file_item.actual_size == file_stat.st_size:
            return file_item.path, f"file size mismatch (expect={file_item.actual_size}B, actual={file_stat.st_size}B)"
        # check digest
        file_digest = Digest.digest_file(digest_algo, str(file_path), use_digest_cache)
        if not file_item.digest == file_digest:
            return file_item.path, f"file {digest_algo.upper()} mismatch (expect={file_item.digest}, actual={file_digest})"

        return None

    def validate_link(link_item: LinkItem) -> Optional[FailedDetail]:
        link_path = catalog_root / link_item.path
        if not link_path.exists(follow_symlinks=False):
            return link_item.path, "link missing"
        if not link_path.is_symlink():
            return link_item.path, "not a link"

        link_stat = link_path.stat(follow_symlinks=False)
        link_mode = link_stat.st_mode & 0o777
        if not link_item.perm == link_mode:
            return link_item.path, f"link mode mismatch (expect={link_item.perm:03o}, actual={link_mode:03o})"
        # check link dest
        link_dest = os.readlink(link_path)
        if not link_item.dest == link_dest:
            return link_item.path, f"link destination mismatch (expect=[{link_item.dest}], actual=[{link_dest}])"

        return None

    def validate_dir(dir_item: DirItem) -> Optional[FailedDetail]:
        dir_path = catalog_root / dir_item.path
        if not dir_path.exists():
            return dir_item.path, "directory missing"
        if dir_path.is_symlink() or not dir_path.is_dir():
            return dir_item.path, "not a directory"

        dir_stat = dir_path.stat()
        dir_mode = dir_stat.st_mode & 0o777
        if not dir_item.perm == dir_mode:
            return dir_item.path, f"directory mode mismatch (expect={dir_item.perm:03o}, actual={dir_mode:03o})"

        return None

    file_problems: FailedList = sorted(
        [x for x in map(validate_file, catalog.files) if x is not None],
        key=lambda _: _[0]
    )
    link_problems: FailedList = sorted(
        [x for x in map(validate_link, catalog.links) if x is not None],
        key=lambda _: _[0]
    )
    dir_problems: FailedList = sorted(
        [x for x in map(validate_dir, catalog.dirs) if x is not None],
        key=lambda _: _[0]
    )

    return file_problems, link_problems, dir_problems


def find_absolute_links(catalog: FileCatalog) -> List[LinkItem]:
    """
    Find the links whose destination is an absolute path.

    FESVR's +chroot only prefixes the target's path strings, so when the host kernel follows an absolute link inside a
    sysroot, it resolves it from the host's root, not the sysroot's.
    """
    return [link for link in catalog.links if os.path.isabs(link.dest)]
