import os
import pathlib
import stat
from datetime import datetime
from typing import Union, Dict, Tuple, List, Optional

from fwm.data_loader.file_catalog import FileCatalog, FileItem, LinkItem, DirItem
from fwm.utils.digest import Digest


def _raise(error: OSError) -> None:
    raise error


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

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False, onerror=_raise):
        curr_dir = pathlib.Path(dirpath).resolve()

        filenames.sort()
        dirnames.sort()
        for name in filenames + dirnames:
            try:
                name.encode("utf-8")
            except UnicodeEncodeError:
                raise NotImplementedError(
                    f"{curr_dir / name!r} cannot be included by the catalog because its name is not UTF-8.")

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


def verify_catalog(catalog: FileCatalog, tree_root: str, use_digest_cache=False, perm_mask: int = 0o777) -> \
        Tuple[FailedList, FailedList, FailedList]:
    """
    Verify a tree against its catalog.

    :param perm_mask: The permission bits to compare, e.g. 0o555 to ignore the write bits of a tree made read-only.
    :return: The problems found with files, links and directories: lists of (path, reason).
    """
    catalog_root = pathlib.Path(tree_root).resolve()
    digest_algo = catalog.digest_algo

    def examine(path: str, kind: str) -> Tuple[Optional[os.stat_result], Optional[FailedDetail]]:
        """
        The lstat of an item of the tree, or (None and) why it can't be had: the item is missing, or out of reach, e.g.
        in a directory this user can't search. (One lstat, not Path.exists(): what exists() does with a permission
        error changed in Python 3.14, which made such an item a crash before and "missing" after.)
        """
        try:
            return os.lstat(catalog_root / path), None
        except (FileNotFoundError, NotADirectoryError):
            return None, (path, f"{kind} missing")
        except OSError as e:
            return None, (path, f"{kind} can't be examined: {e.strerror}")

    def validate_file(file_item: FileItem) -> Optional[FailedDetail]:
        file_stat, problem = examine(file_item.path, "file")
        if file_stat is None:
            return problem
        if not stat.S_ISREG(file_stat.st_mode):
            return file_item.path, "not a file"

        file_mode = file_stat.st_mode & 0o777
        if not file_item.perm & perm_mask == file_mode & perm_mask:
            return file_item.path, f"file mode mismatch (expect={file_item.perm:03o}, actual={file_mode:03o})"
        if not file_item.actual_size == file_stat.st_size:
            return file_item.path, f"file size mismatch (expect={file_item.actual_size}B, actual={file_stat.st_size}B)"
        # check digest
        try:
            file_digest = Digest.digest_file(digest_algo, str(catalog_root / file_item.path), use_digest_cache)
        except OSError as e:
            return file_item.path, f"file can't be read: {e.strerror}"
        if not file_item.digest == file_digest:
            return (file_item.path,
                    f"file {digest_algo.upper()} mismatch (expect={file_item.digest}, actual={file_digest})")

        return None

    def validate_link(link_item: LinkItem) -> Optional[FailedDetail]:
        link_stat, problem = examine(link_item.path, "link")
        if link_stat is None:
            return problem
        if not stat.S_ISLNK(link_stat.st_mode):
            return link_item.path, "not a link"

        link_mode = link_stat.st_mode & 0o777
        if not link_item.perm & perm_mask == link_mode & perm_mask:
            return link_item.path, f"link mode mismatch (expect={link_item.perm:03o}, actual={link_mode:03o})"
        # check link dest
        link_dest = os.readlink(catalog_root / link_item.path)
        if not link_item.dest == link_dest:
            return link_item.path, f"link destination mismatch (expect=[{link_item.dest}], actual=[{link_dest}])"

        return None

    def validate_dir(dir_item: DirItem) -> Optional[FailedDetail]:
        dir_stat, problem = examine(dir_item.path, "directory")
        if dir_stat is None:
            return problem
        if not stat.S_ISDIR(dir_stat.st_mode):
            return dir_item.path, "not a directory"

        dir_mode = dir_stat.st_mode & 0o777
        if not dir_item.perm & perm_mask == dir_mode & perm_mask:
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


def catalog_differences(a: FileCatalog, b: FileCatalog) -> List[str]:
    """
    The paths whose entries differ between two catalogs (type, permission, size, digest or link destination), ignoring
    when the catalogs were created. Empty if the catalogs describe the same tree.
    """
    if a.digest_algo != b.digest_algo:
        raise ValueError(f"cannot compare catalogs of different digest algorithms: {a.digest_algo}, {b.digest_algo}")

    def entries(c: FileCatalog) -> Dict[str, Tuple]:
        result: Dict[str, Tuple] = {}
        for f in c.files:
            result[f.path] = ("file", f.perm, f.actual_size, f.digest.lower())
        for link in c.links:
            result[link.path] = ("link", link.perm, link.dest)
        for d in c.dirs:
            result[d.path] = ("dir", d.perm)
        return result

    ea, eb = entries(a), entries(b)
    return sorted(p for p in ea.keys() | eb.keys() if ea.get(p) != eb.get(p))
