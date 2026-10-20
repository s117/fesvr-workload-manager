import os
from typing import Sequence

from fwm.repo.errors import FwmValidationError
from fwm.repo.repo import Repo
from fwm.repo.transfer import push, TransferResult
from fwm.utils.fs import remove_tree


def create_subrepo(source: Repo, path: str, entry_names: Sequence[str], traces: bool = True,
                   checkpoints: bool = True) -> TransferResult:
    """
    Create a repository at path with entry points of source, and what they need: a new repository that source pushes
    to (see transfer()). If the copy fails, the new repository is removed, so that it is created whole or not at all.

    :raise FwmValidationError: path is inside source, or exists and is not an empty directory.
    """
    source_path = os.path.realpath(source.path)
    if os.path.commonpath([os.path.realpath(path), source_path]) == source_path:
        raise FwmValidationError(f"{path} is inside the repository {source.path}: create it somewhere else")
    existed = os.path.isdir(path)
    Repo.init(path, bind=False).close()
    try:
        return push(source, path, entry_names, traces=traces, checkpoints=checkpoints)
    except BaseException:
        remove_tree(path)
        if existed:
            os.mkdir(path)  # it was an empty directory
        raise
