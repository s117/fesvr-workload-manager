from datetime import datetime

import peewee as pw

# Bumped on any change to the tables in schema.py. A repository whose database has another version is refused.
SCHEMA_VERSION = 1

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


def open_db(path: str, create: bool = False) -> pw.SqliteDatabase:
    """
    Open the repository database and bind the models to it.

    Several fwm processes may write to one repository at the same time (e.g. the jobs of fwm-bootstrap), so the database
    runs in WAL mode, waits for a busy database instead of failing at once, and starts every transaction with the write
    lock held (BEGIN IMMEDIATE), which avoids failing on a lock upgrade in the middle of a transaction.

    :param path: Path of the SQLite file, or ":memory:".
    :param create: Create the tables and record the schema version (for a new repository).
    :return: The opened database.
    :raise SchemaVersionError: The database was created for another schema version.
    """
    from fwm.database.schema import ALL_MODELS, SchemaInfo

    db = pw.SqliteDatabase(path, pragmas={
        "journal_mode": "wal",
        "busy_timeout": 30000,
        "foreign_keys": 1,
        "synchronous": "normal",
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
