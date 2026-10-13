import peewee as pw

from fwm.database.custom_field import TimestampTzField
from fwm.database.db import BaseModel, now_tz

# Length of a hex SHA256 digest (passed positionally: the peewee stubs don't accept max_length= for FixedCharField)
SHA256_HEX_LEN = 64


class SchemaInfo(BaseModel):
    """
    A single row holding the version of this schema (db.SCHEMA_VERSION).
    """
    version = pw.IntegerField()


class TreeCatalog(BaseModel):
    """
    A file catalog of a directory tree: every file, link and directory in it, with their permissions, and the digest
    of every file. See data_loader.file_catalog.FileCatalog, its in-memory form.
    """
    # Digest algorithm used in this catalog. fwm always uses sha256.
    digest_algo = pw.CharField()

    # Creation time.
    creation_time = TimestampTzField(default=now_tz)


class CatalogFile(BaseModel):
    # Belongs to which TreeCatalog.
    catalog = pw.ForeignKeyField(TreeCatalog, backref='files', on_delete='CASCADE')

    # File path, relative to the tree root.
    path = pw.CharField()

    # File permission.
    perm = pw.IntegerField()

    # The actual size of the files in bytes.
    # Note: this is not the allocated disk space, which is a variable of the actual size and file system.
    actual_size = pw.BigIntegerField()

    # Digest of the file.
    digest = pw.CharField()

    class Meta:
        indexes = ((('catalog', 'path'), True),)


class CatalogLink(BaseModel):
    # Belongs to which TreeCatalog.
    catalog = pw.ForeignKeyField(TreeCatalog, backref='links', on_delete='CASCADE')

    # Link path, relative to the tree root.
    path = pw.CharField()

    # Link permission.
    perm = pw.IntegerField()

    # Link destination: the link's own text.
    dest = pw.CharField()

    class Meta:
        indexes = ((('catalog', 'path'), True),)


class CatalogDir(BaseModel):
    # Belongs to which TreeCatalog.
    catalog = pw.ForeignKeyField(TreeCatalog, backref='dirs', on_delete='CASCADE')

    # Dir path, relative to the tree root.
    path = pw.CharField()

    # Dir permission.
    perm = pw.IntegerField()

    class Meta:
        indexes = ((('catalog', 'path'), True),)


class Workload(BaseModel):
    """
    A workload groups the entry points running the same program, possibly on different sysroots. It is created by its
    first entry point and removed with its last one.
    """
    # Unique name of the workload.
    name = pw.CharField(unique=True, index=True)

    # Optional description.
    description = pw.TextField(null=True)

    # Creation time.
    creation_time = TimestampTzField(default=now_tz)


class Sysroot(BaseModel):
    """
    A sysroot containing the files of one or more workloads, including: workload executables, input data, proxy
    kernels, any other supportive files.
    """
    # Unique name of the sysroot.
    name = pw.CharField(unique=True, index=True)

    # Optional description.
    description = pw.TextField(null=True)

    # A file catalog of the sysroot tree. Relative to the sysroot folder.
    file_catalog = pw.ForeignKeyField(TreeCatalog)

    # Creation time.
    creation_time = TimestampTzField(default=now_tz)


class NativeEntry(BaseModel):
    """
    An entry point describes how to launch a workload in a sysroot. See data_loader.entry_point.WorkloadEntry.
    """
    # The name of the entry.
    name = pw.CharField(unique=True, index=True)

    # The workload it runs.
    workload = pw.ForeignKeyField(Workload, backref='entries')

    # The sysroot it runs in.
    sysroot = pw.ForeignKeyField(Sysroot, backref='entries')

    # Command to launch the workload.
    command = pw.CharField()

    # The working directory, as given in the entry point.
    working_directory = pw.CharField()

    # If not null, the file to be redirected to the program as STDIN, as given in the entry point.
    stdin_file = pw.CharField(null=True)

    # The ELF the simulator boots (usually the proxy kernel), as given in the entry point.
    boot_elf = pw.CharField()

    # Amount of memory required to launch the workload. In MBytes.
    mem_mb = pw.IntegerField()

    # Number of processors to simulate.
    nprocs = pw.IntegerField()

    # Optional description.
    description = pw.TextField(null=True)

    # Creation time.
    creation_time = TimestampTzField(default=now_tz)


class DecoupledTrace(BaseModel):
    """
    A device traffic recording (+dev-traffic-record) of a run of an entry point. It replays without the sysroot.
    """
    # UUID of the trace, also its folder name in the repository.
    uuid = pw.UUIDField(unique=True, index=True)

    # Which entry produced this trace?
    native_entry = pw.ForeignKeyField(NativeEntry, backref='traces')

    # File catalog (relative to the trace folder).
    file_catalog = pw.ForeignKeyField(TreeCatalog)

    # Memory size of the recorded system in MB (target_spec.data).
    mem_mb = pw.IntegerField()

    # Number of HARTs of the recorded system (target_spec.data).
    num_harts = pw.IntegerField()

    # SHA256 of the boot ELF (target_spec.data), also the SHA256 of the recording's boot.elf.
    boot_elf_sha256 = pw.FixedCharField(SHA256_HEX_LEN)

    # The program's exit status, read from the recording. Null if the run never exited (a truncated recording).
    exit_status = pw.IntegerField(null=True)

    # Optional description.
    description = pw.TextField(null=True)

    # Creation time.
    creation_time = TimestampTzField(default=now_tz)


class TraceHart(BaseModel):
    """
    The per-HART part of a trace.
    """
    # Belongs to which trace.
    trace = pw.ForeignKeyField(DecoupledTrace, backref='harts', on_delete='CASCADE')

    # HART ID.
    hart_id = pw.IntegerField()

    # SHA256 of the HART's recording (its device_traffic_hart<N>.sha256), which identifies it.
    recording_sha256 = pw.FixedCharField(SHA256_HEX_LEN)

    # Number of commands recorded for the HART.
    num_commands = pw.BigIntegerField()

    # The HART's retired-instruction count at the end of the recording run (+final-state-dump), if known.
    instret = pw.BigIntegerField(null=True)

    class Meta:
        indexes = ((('trace', 'hart_id'), True),)


class DecoupledCheckpoint(BaseModel):
    """
    A checkpoint created by replaying a trace (+create-checkpoint). It loads only together with that trace.
    """
    # Name of the checkpoint, also its file name in the repository.
    name = pw.CharField()

    # Which trace is required to load this checkpoint?
    trace = pw.ForeignKeyField(DecoupledTrace, backref='checkpoints')

    # File catalog (relative to the checkpoint folder).
    file_catalog = pw.ForeignKeyField(TreeCatalog)

    # Checkpoint format version, from the header.
    format_version = pw.IntegerField()

    # Host-independent digest: the SHA256 of the output of riscv-isa-sim's scripts/checkpoint-digest.py.
    content_sha256 = pw.FixedCharField(SHA256_HEX_LEN)

    # SimPoint weight of the interval the checkpoint starts, if it is a SimPoint checkpoint.
    simpoint_weight = pw.FloatField(null=True)

    # Optional description.
    description = pw.TextField(null=True)

    # Creation time.
    creation_time = TimestampTzField(default=now_tz)

    class Meta:
        indexes = ((('trace', 'name'), True),)


class CheckpointHart(BaseModel):
    """
    The per-HART part of a checkpoint, from its header.
    """
    # Belongs to which checkpoint.
    checkpoint = pw.ForeignKeyField(DecoupledCheckpoint, backref='harts', on_delete='CASCADE')

    # HART ID.
    hart_id = pw.IntegerField()

    # SHA256 of the HART's recording the checkpoint was created from.
    recording_sha256 = pw.FixedCharField(SHA256_HEX_LEN)

    # Number of commands already serviced: the replay resumes at this command index.
    cmd_index = pw.BigIntegerField()

    # Version of the hart state format.
    hart_state_version = pw.IntegerField()

    # The HART's CSR_COUNT, decoded from the saved hart state: the retired-instruction count where the checkpoint is.
    csr_count = pw.BigIntegerField()

    class Meta:
        indexes = ((('checkpoint', 'hart_id'), True),)


ALL_MODELS = [
    SchemaInfo,
    TreeCatalog, CatalogFile, CatalogLink, CatalogDir,
    Workload, Sysroot, NativeEntry,
    DecoupledTrace, TraceHart,
    DecoupledCheckpoint, CheckpointHart,
]
