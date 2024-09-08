import os
import pathlib
from datetime import datetime
from typing import Union, Dict, List

from fwm.data_loader.file_catalog import FileCatalog
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
        symlink_resolved_dest = link_full.resolve(strict=False)
        if root in symlink_resolved_dest.parents:
            link_dest = symlink_resolved_dest.relative_to(root)
        else:
            link_dest = symlink_resolved_dest
        links.append({
            "path": str(link_full.relative_to(root)),
            "perm": link_full.stat(follow_symlinks=False).st_mode & 0o777,
            "dest": str(link_dest),
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
