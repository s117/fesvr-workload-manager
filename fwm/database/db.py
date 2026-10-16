import os
import urllib.parse
from datetime import datetime

import peewee as pw

# Bumped on any change to the tables in schema.py. A repository whose database has another version is refused.
SCHEMA_VERSION = 6

# How long a command waits for another one to release the database, in milliseconds
BUSY_TIMEOUT_MS = 30000

# The database the models are bound to, set by open_db().
database_proxy = pw.DatabaseProxy()


class BaseModel(pw.Model):
    class Meta:
        database = database_proxy


def now_tz() -> datetime:
    """The current time, timezone-aware."""
    return datetime.now().astimezone()


class SchemaVersionError(RuntimeError):
    pass


def open_db(path: str, create: bool = False, readonly: bool = False) -> pw.SqliteDatabase:
    """
    Open the repository database and bind the models to it.

    The database uses SQLite's rollback journal, not WAL: WAL needs every process using the database to run on one host,
    while a repository may sit on a shared filesystem (e.g. NFS) and be read from several hosts. Writers wait for a busy
    database instead of failing at once, and start every transaction with the write lock held (BEGIN IMMEDIATE), which
    avoids failing on a lock upgrade in the middle of a transaction.

    :param path: Path of the SQLite file, or ":memory:".
    :param create: Create the tables and record the schema version (for a new repository).
    :param readonly: Open the database read-only, so that it can be read without write permission. Any write fails.
    :return: The opened database.
    :raise SchemaVersionError: The database was created for another schema version.
    """
    from fwm.database.schema import ALL_MODELS, SchemaInfo

    if readonly:
        assert not create
        db = pw.SqliteDatabase(f"file:{urllib.parse.quote(os.path.abspath(path))}?mode=ro", uri=True, pragmas={
            "busy_timeout": BUSY_TIMEOUT_MS,
            "foreign_keys": 1,
        })
    else:
        db = pw.SqliteDatabase(path, pragmas={
            "journal_mode": "delete",
            "busy_timeout": BUSY_TIMEOUT_MS,
            "foreign_keys": 1,
        }, lock_type="IMMEDIATE")
    database_proxy.initialize(db)
    db.connect(reuse_if_open=True)

    if create:
        with db.atomic():
            db.create_tables(ALL_MODELS)
            SchemaInfo.create(version=SCHEMA_VERSION)
    else:
        version = None
        if db.table_exists(SchemaInfo._meta.table_name):  # type: ignore[attr-defined]
            row = SchemaInfo.get_or_none()
            version = row.version if row else None
        if version != SCHEMA_VERSION:
            db.close()
            raise SchemaVersionError(
                f"the database {path} has schema version {version}, but this fwm uses version {SCHEMA_VERSION}")
    return db
