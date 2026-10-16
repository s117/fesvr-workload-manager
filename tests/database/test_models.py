import sqlite3
import uuid
from datetime import datetime

import peewee as pw
import pytest

from fwm.data_loader.file_catalog import FileCatalog
from fwm.database.catalog_store import save_catalog, load_catalog, delete_catalog
from fwm.database.db import open_db, SchemaVersionError, SCHEMA_VERSION
from fwm.database.schema import (
    SchemaInfo, TreeCatalog, CatalogFile, CatalogLink, CatalogDir, Workload, Sysroot, NativeEntry, DecoupledTrace,
    TraceHart, DecoupledCheckpoint, CheckpointHart, ALL_MODELS
)
from fwm.version import fwm_version

SHA_A = "a" * 64
SHA_B = "b" * 64


@pytest.fixture
def db():
    database = open_db(":memory:", create=True)
    yield database
    database.close()


def make_catalog(num_files: int = 3) -> FileCatalog:
    return FileCatalog(
        creation_time=datetime.now().astimezone(),
        digest_algo="sha256",
        files=[{"path": f"dir/file{i}", "perm": 0o644, "actual_size": i, "digest": SHA_A}  # type: ignore
               for i in range(num_files)],
        links=[{"path": "link", "perm": 0o777, "dest": "/abs/dest"}],  # type: ignore
        dirs=[{"path": "dir", "perm": 0o755}],  # type: ignore
    )


def make_entry(name: str = "entry", workload: str = "wl", sysroot: str = "sr") -> NativeEntry:
    wl, _ = Workload.get_or_create(name=workload)
    sr = Sysroot.get_or_none(name=sysroot) or Sysroot.create(name=sysroot, file_catalog=save_catalog(make_catalog()))
    return NativeEntry.create(name=name, workload=wl, sysroot=sr, command="prog arg", working_directory="/app",
                              stdin_file=None, boot_elf="/boot/pk", mem_mb=256, nprocs=1)


def make_trace(entry: NativeEntry) -> DecoupledTrace:
    trace = DecoupledTrace.create(uuid=uuid.uuid4(), native_entry=entry, file_catalog=save_catalog(make_catalog()),
                                  mem_mb=256, num_harts=1, boot_elf_sha256=SHA_A, exit_status=0)
    TraceHart.create(trace=trace, hart_id=0, recording_sha256=SHA_B, num_commands=10, instret=1000)
    return trace


def make_checkpoint(trace: DecoupledTrace, name: str = "ckpt") -> DecoupledCheckpoint:
    ckpt = DecoupledCheckpoint.create(trace=trace, name=name, file_catalog=save_catalog(make_catalog(1)),
                                      format_version=1, content_sha256=SHA_A, simpoint_weight=0.5,
                                      requested_count=500)
    CheckpointHart.create(checkpoint=ckpt, hart_id=0, recording_sha256=SHA_B, cmd_index=5, hart_state_version=1,
                          csr_count=500)
    return ckpt


def test_schema_version_recorded(db):
    assert SchemaInfo.get().version == SCHEMA_VERSION


def test_open_db_rejects_other_version(tmp_path):
    path = str(tmp_path / "fwm.db")
    open_db(path, create=True).close()

    open_db(path).close()  # same version

    db = open_db(path)
    SchemaInfo.update(version=SCHEMA_VERSION + 1).execute()
    db.close()
    with pytest.raises(SchemaVersionError):
        open_db(path)


def test_open_db_rejects_uninitialized(tmp_path):
    with pytest.raises(SchemaVersionError):
        open_db(str(tmp_path / "empty.db"))


def test_open_db_pragmas(tmp_path):
    db = open_db(str(tmp_path / "fwm.db"), create=True)
    # a rollback journal, not WAL, which needs every process on one host
    assert db.execute_sql("pragma journal_mode").fetchone()[0] == "delete"
    assert db.execute_sql("pragma foreign_keys").fetchone()[0] == 1
    assert db.execute_sql("pragma busy_timeout").fetchone()[0] == 30000
    db.close()


def test_catalog_round_trip(db):
    catalog = make_catalog(1200)  # more than one insert batch
    tree = save_catalog(catalog)
    loaded = load_catalog(TreeCatalog.get_by_id(tree.id))
    assert loaded.creation_time == catalog.creation_time
    assert loaded.digest_algo == catalog.digest_algo
    assert sorted(loaded.files, key=lambda f: f.path) == sorted(catalog.files, key=lambda f: f.path)
    assert loaded.links == catalog.links
    assert loaded.dirs == catalog.dirs


def test_catalog_paths_unique(db):
    tree = save_catalog(make_catalog())
    with pytest.raises(pw.IntegrityError):
        CatalogDir.create(catalog=tree, path="dir", perm=0o700)


def test_delete_catalog_cascades(db):
    tree = save_catalog(make_catalog())
    delete_catalog(tree)
    assert CatalogFile.select().count() == 0
    assert CatalogLink.select().count() == 0
    assert CatalogDir.select().count() == 0


def test_hierarchy(db):
    entry = make_entry()
    trace = make_trace(entry)
    ckpt = make_checkpoint(trace)
    assert [e.name for e in Workload.get(name="wl").entries] == ["entry"]
    assert [e.name for e in Sysroot.get(name="sr").entries] == ["entry"]
    assert [t.uuid for t in entry.traces] == [trace.uuid]
    assert [h.instret for h in trace.harts] == [1000]
    assert [c.name for c in trace.checkpoints] == ["ckpt"]
    assert [h.csr_count for h in ckpt.harts] == [500]


def test_workloads_and_sysroots_many_to_many(db):
    make_entry("bfs-road", workload="bfs", sysroot="road")
    make_entry("bfs-web", workload="bfs", sysroot="web")
    make_entry("pr-road", workload="pr", sysroot="road")
    assert sorted(e.name for e in Workload.get(name="bfs").entries) == ["bfs-road", "bfs-web"]
    assert sorted(e.name for e in Sysroot.get(name="road").entries) == ["bfs-road", "pr-road"]


def test_parent_with_children_cannot_be_deleted(db):
    entry = make_entry()
    trace = make_trace(entry)
    make_checkpoint(trace)
    for parent in (Sysroot.get(name="sr"), Workload.get(name="wl"), entry, trace):
        with pytest.raises(pw.IntegrityError):
            parent.delete_instance()


def test_harts_cascade(db):
    trace = make_trace(make_entry())
    ckpt = make_checkpoint(trace)
    ckpt.delete_instance()
    assert CheckpointHart.select().count() == 0
    trace.delete_instance()
    assert TraceHart.select().count() == 0


def test_unique_names(db):
    entry = make_entry()
    trace = make_trace(entry)
    make_checkpoint(trace, "ckpt")
    with pytest.raises(pw.IntegrityError):
        make_entry("entry")
    with pytest.raises(pw.IntegrityError):
        make_checkpoint(trace, "ckpt")
    with pytest.raises(pw.IntegrityError):
        TraceHart.create(trace=trace, hart_id=0, recording_sha256=SHA_B, num_commands=1)
    # a checkpoint name only needs to be unique within its trace
    make_checkpoint(make_trace(entry), "ckpt")


def test_creation_time_defaults_to_now(db):
    before = datetime.now().astimezone()
    entry = make_entry()
    assert NativeEntry.get_by_id(entry.id).creation_time >= before.replace(microsecond=0)
    assert NativeEntry.get_by_id(entry.id).creation_time.tzinfo is not None


def test_read_only(tmp_path):
    path = str(tmp_path / "fwm.db")
    db = open_db(path, create=True)
    Workload.create(name="w")
    db.close()

    db = open_db(path, readonly=True)
    assert [w.name for w in Workload.select()] == ["w"]
    with pytest.raises(pw.OperationalError, match="readonly"):
        Workload.create(name="x")
    db.close()


def test_catalog_with_old_sqlite_variable_limit(db):
    # SQLite before 3.32 (e.g. RHEL 8's) allows only 999 bound variables per statement
    db.connection().setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
    catalog = make_catalog(1200)
    tree = save_catalog(catalog)
    assert len(load_catalog(TreeCatalog.get_by_id(tree.id)).files) == 1200


def test_provenance_and_simpoint_fields(db):
    catalog = save_catalog(make_catalog())
    sysroot = Sysroot.create(name="s", file_catalog=catalog)
    entry = NativeEntry.create(name="e", workload=Workload.create(name="w"), sysroot=sysroot, command="p",
                               working_directory="/", boot_elf="/pk", mem_mb=1, nprocs=1)
    trace = DecoupledTrace.create(uuid=uuid.uuid4(), native_entry=entry, file_catalog=catalog, mem_mb=1, num_harts=1,
                                  boot_elf_sha256="0" * 64)
    assert trace.sim_version is None
    assert trace.fwm_version == fwm_version()
    ckpt = DecoupledCheckpoint.create(name="c", trace=trace, file_catalog=catalog, format_version=1,
                                      content_sha256="0" * 64, simpoint_index=3, simpoint_interval=100000000,
                                      simpoint_max_k=10, requested_count=300000017)
    ckpt = DecoupledCheckpoint.get_by_id(ckpt.id)
    assert (ckpt.simpoint_index, ckpt.simpoint_interval, ckpt.simpoint_max_k, ckpt.requested_count) == \
        (3, 100000000, 10, 300000017)
    assert (ckpt.validation_status, ckpt.validation_time, ckpt.validation_detail) == (None, None, None)
    # every checkpoint is imported from its description file, which gives the count it was requested at
    with pytest.raises(pw.IntegrityError, match="requested_count"):
        DecoupledCheckpoint.create(name="d", trace=trace, file_catalog=catalog, format_version=1,
                                   content_sha256="0" * 64)


def test_no_redundant_index(db):
    """No index is the leading columns of another index of its table: that one serves its lookups already."""
    indexes = {}
    for model in ALL_MODELS:
        table = model._meta.table_name  # type: ignore[attr-defined]
        for (name,) in db.execute_sql(f'SELECT name FROM pragma_index_list("{table}")').fetchall():
            columns = [c for (c,) in db.execute_sql(f'SELECT name FROM pragma_index_info("{name}")').fetchall()]
            indexes[name] = (table, columns)
    assert indexes  # the query found them
    for name, (table, columns) in indexes.items():
        for other, (other_table, other_columns) in indexes.items():
            if other != name and other_table == table:
                assert other_columns[:len(columns)] != columns, f"{name} {columns} is redundant with {other}"


def test_catalog_lookup_uses_unique_index(db):
    plan = db.execute_sql("EXPLAIN QUERY PLAN SELECT * FROM catalogfile WHERE catalog_id = 1 ORDER BY path").fetchall()
    assert "catalogfile_catalog_id_path" in plan[-1][-1]
