import contextlib
import os
import re
import uuid
from typing import Callable, Iterator, List, Tuple

import peewee as pw

from fwm.database.db import open_db, SchemaVersionError
from fwm.repo.errors import FwmValidationError, NotFoundError
from fwm.utils.fs import remove_tree, set_tree_readonly, set_tree_writable

REPO_DB_FILE = "fwm.db"
SYSROOTS_DIR = "sysroots"
TRACES_DIR = "traces"
CHECKPOINTS_DIR = "checkpoints"
STAGING_DIR = ".staging"

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
    def open(cls, path: str) -> "Repo":
        expected = (REPO_DB_FILE, SYSROOTS_DIR, TRACES_DIR, CHECKPOINTS_DIR, STAGING_DIR)
        missing = [p for p in expected if not os.path.exists(os.path.join(path, p))]
        if missing:
            raise NotFoundError(f"{path} is not an fwm repository: it has no {', '.join(missing)}")
        try:
            return cls(path, open_db(os.path.join(path, REPO_DB_FILE)))
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
        """
        path = os.path.join(self.path, STAGING_DIR, uuid.uuid4().hex)
        try:
            yield path
        finally:
            remove_tree(path)

    @contextlib.contextmanager
    def removal(self) -> Iterator[Callable[[str], None]]:
        """
        Collect the paths to remove along with database rows. Use it around the database transaction:

            with repo.removal() as remove, repo.db.atomic():
                row.delete_instance()
                remove(path)

        Each path is moved to the staging area at once. If the block fails (and the transaction is rolled back), the
        paths are moved back and made read-only again; otherwise they are deleted.
        """
        moved: List[Tuple[str, str]] = []

        def remove(path: str) -> None:
            if not os.path.lexists(path):
                return
            retired = os.path.join(self.path, STAGING_DIR, uuid.uuid4().hex + ".removed")
            if os.path.isdir(path) and not os.path.islink(path):
                set_tree_writable(path)  # a directory must be writable to be moved to another parent
            os.rename(path, retired)
            moved.append((path, retired))

        try:
            yield remove
        except BaseException:
            for path, retired in reversed(moved):
                os.rename(retired, path)
                set_tree_readonly(path)
            raise
        for _, retired in moved:
            remove_tree(retired)

    def move_into_place(self, staged: str, final: str) -> None:
        """
        Rename a staged file or tree to its place in the repository. Call it inside the transaction that adds its rows.
        """
        if os.path.lexists(final):
            raise FwmValidationError(f"cannot import to {final}: it already exists")
        os.rename(staged, final)
