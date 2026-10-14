import contextlib
import fcntl
import os
import re
import uuid
from typing import Callable, Iterator, List, Tuple

import peewee as pw

from fwm.database.db import open_db, SchemaVersionError
from fwm.repo.errors import FwmValidationError, NotFoundError
from fwm.utils.fs import remove_tree, set_tree_readonly
from fwm.utils.log import Logger

REPO_DB_FILE = "fwm.db"
SYSROOTS_DIR = "sysroots"
TRACES_DIR = "traces"
CHECKPOINTS_DIR = "checkpoints"
STAGING_DIR = ".staging"
STAGING_LOCK_SUFFIX = ".lock"

# The digest algorithm of every catalog fwm creates. It is the one FESVR uses for the boot ELF, so the catalog's digest
# of a sysroot's boot ELF compares directly with a recording's.
DIGEST_ALGO = "sha256"

# The content of a repository is made read-only, so its write bits are not compared with its catalogs
REPO_PERM_MASK = 0o555

# Names of sysroots, workloads and entry points: they are used as file names and in Makefiles
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def check_name(kind: str, name: str) -> None:
    if not _NAME_RE.match(name) or name in (".", ".."):
        raise FwmValidationError(
            f"invalid {kind} name '{name}': a name may contain only letters, digits, '.', '_' and '-'")


class Repo:
    """
    An fwm workload repository:

        <repo>/
          fwm.db                          # metadata and catalogs
          sysroots/<sysroot-name>/        # pristine sysroots, read-only
          traces/<uuid>/                  # device traffic recordings, read-only
          checkpoints/<uuid>/<ckpt-name>  # checkpoints of the trace <uuid>, read-only
          .staging/                       # imports land here first, then are renamed into place
    """

    def __init__(self, path: str, db: pw.SqliteDatabase):
        self.path = os.path.abspath(path)
        self.db = db

    @classmethod
    def init(cls, path: str) -> "Repo":
        if os.path.exists(path) and (not os.path.isdir(path) or os.listdir(path)):
            raise FwmValidationError(f"cannot create a repository at {path}: it exists and is not an empty directory")
        os.makedirs(path, exist_ok=True)
        for d in (SYSROOTS_DIR, TRACES_DIR, CHECKPOINTS_DIR, STAGING_DIR):
            os.mkdir(os.path.join(path, d))
        return cls(path, open_db(os.path.join(path, REPO_DB_FILE), create=True))

    @classmethod
    def open(cls, path: str, readonly: bool = False) -> "Repo":
        """
        Open a repository.

        :param readonly: Only read it: this works without write permission, but nothing can be changed.
        """
        expected = (REPO_DB_FILE, SYSROOTS_DIR, TRACES_DIR, CHECKPOINTS_DIR, STAGING_DIR)
        missing = [p for p in expected if not os.path.exists(os.path.join(path, p))]
        if missing:
            raise NotFoundError(f"{path} is not an fwm repository: it has no {', '.join(missing)}")
        if not readonly:
            # SQLite writes its rollback journal next to the database, and imports go through the staging area
            for p in ("", REPO_DB_FILE, STAGING_DIR):
                if not os.access(os.path.join(path, p), os.W_OK):
                    raise FwmValidationError(f"the repository {path} is read-only for you: it can't be changed")
        try:
            return cls(path, open_db(os.path.join(path, REPO_DB_FILE), readonly=readonly))
        except SchemaVersionError as e:
            raise FwmValidationError(str(e))

    def close(self) -> None:
        self.db.close()

    def sysroot_dir(self, name: str) -> str:
        return os.path.join(self.path, SYSROOTS_DIR, name)

    def trace_dir(self, trace_uuid: uuid.UUID) -> str:
        return os.path.join(self.path, TRACES_DIR, str(trace_uuid))

    def checkpoint_dir(self, trace_uuid: uuid.UUID) -> str:
        return os.path.join(self.path, CHECKPOINTS_DIR, str(trace_uuid))

    def checkpoint_path(self, trace_uuid: uuid.UUID, name: str) -> str:
        return os.path.join(self.checkpoint_dir(trace_uuid), name)

    @contextlib.contextmanager
    def staging(self) -> Iterator[str]:
        """
        A fresh path in the staging area, inside the repository so that it can be renamed into place. Whatever is left
        at the path is removed at the end.

        While the block runs, a lock file next to the path is held (flock), so that `fwm repo gc` can tell the staging
        of a live import from the leftover of one that was killed.
        """
        path = os.path.join(self.path, STAGING_DIR, uuid.uuid4().hex)
        lock_fd = os.open(path + STAGING_LOCK_SUFFIX, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield path
        finally:
            remove_tree(path)
            os.unlink(path + STAGING_LOCK_SUFFIX)
            os.close(lock_fd)

    @contextlib.contextmanager
    def importing(self) -> Iterator[Callable[[str, str], None]]:
        """
        The transaction of an import, with a function that renames a staged file or tree to its place:

            with repo.importing() as place:
                Sysroot.create(...)
                place(staged, final)

        If the block or the commit fails, the database is rolled back and the placed items are renamed back to the
        staging area, so the import leaves nothing behind. (If the process dies before the commit, they remain as items
        no row refers to, which `fwm repo gc` removes.) After the commit, they are made read-only.
        """
        placed: List[Tuple[str, str]] = []

        def place(staged: str, final: str) -> None:
            if os.path.lexists(final):
                raise FwmValidationError(f"cannot import to {final}: it already exists")
            os.rename(staged, final)
            placed.append((staged, final))

        try:
            with self.db.atomic():
                yield place
        except BaseException:
            for staged, final in reversed(placed):
                try:
                    os.rename(final, staged)
                except OSError:
                    pass  # left for `fwm repo gc`
            raise
        for _, final in placed:
            set_tree_readonly(final)

    @contextlib.contextmanager
    def removal(self) -> Iterator[Callable[[str], None]]:
        """
        The transaction of a removal, with a function that schedules a path to be deleted:

            with repo.removal() as remove:
                row.delete_instance()
                remove(path)

        The paths are deleted only after the commit, so a failure or a crash never leaves rows whose files are gone. A
        path that can't be deleted then (or that a crash left) is reported, and left for `fwm repo gc`.
        """
        paths: List[str] = []
        with self.db.atomic():
            yield paths.append
        for path in paths:
            try:
                remove_tree(path)
            except OSError as e:
                Logger.get_logger().warning(f"cannot delete {path} ({e}); `fwm repo gc` will remove it")
