import os
from typing import Optional, Sequence

from fwm.repo.errors import FwmValidationError
from fwm.repo.repo import Repo
from fwm.repo.transfer import push, TransferResult
from fwm.utils.fs import remove_tree
from fwm.utils.log import Logger


def _first_missing(path: str) -> Optional[str]:
    """The topmost of path and its ancestors that doesn't exist, which creating path creates; None if path exists."""
    missing = None
    while not os.path.lexists(path):
        missing = path
        path = os.path.dirname(path)
    return missing


def _undo_init(path: str, created: Optional[str]) -> None:
    """
    Remove the repository created at path, and the directories created for it, up to created, while they are empty:
    another command may have put something in them meanwhile, e.g. a second subrepo next to this one. If path was an
    existing empty directory (created is None), only its content is removed, so it keeps its permissions and group.
    """
    if created is None:
        for name in os.listdir(path):
            remove_tree(os.path.join(path, name))
        return
    remove_tree(path)
    directory = path
    while directory != created:
        directory = os.path.dirname(directory)
        try:
            os.rmdir(directory)
        except OSError:  # not empty: it is used by something else now
            return


def create_subrepo(source: Repo, path: str, entry_names: Sequence[str], traces: bool = True,
                   checkpoints: bool = True) -> TransferResult:
    """
    Create a repository at path with entry points of source, and what they need: a new repository that source pushes
    to (see transfer()). If the copy fails, the new repository is removed, so that it is created whole or not at all;
    an empty directory it was created in is left empty.

    :raise FwmValidationError: path is inside source.
    :raise AlreadyExistsError: path exists and is not an empty directory.
    """
    path = os.path.realpath(path)  # a link to an empty directory: create (and on failure, remove) the repository there
    source_path = os.path.realpath(source.path)
    if os.path.commonpath([path, source_path]) == source_path:
        raise FwmValidationError(f"{path} is inside the repository {source.path}: create it somewhere else")
    created = _first_missing(path)
    Repo.init(path, bind=False).close()
    try:
        return push(source, path, entry_names, traces=traces, checkpoints=checkpoints)
    except BaseException:
        try:
            _undo_init(path, created)
        except OSError as e:  # the failure to report is the copy's
            Logger.get_logger().warning(f"cannot remove the new repository {path} ({e}): remove it yourself")
        raise
