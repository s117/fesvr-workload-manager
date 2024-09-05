import peewee as pw

from fwm.database.custom_field import TimestampTzField


class TreeCatalog(pw.Model):
    # Creation time.
    creation_time = TimestampTzField()

    # Last update time.
    last_update_time = TimestampTzField()


class FileItem(pw.Model):
    # File path.
    location = pw.CharField()

    # The actual size of the files in bytes.
    # Note: this is not the allocated disk space, which is a variable of the actual size and file system.
    actual_size = pw.BigIntegerField()

    # SHA256 of the file.
    sha256_digest = pw.FixedCharField(max_length=64)

    # Belongs to which TreeCatalog.
    catalog = pw.ForeignKeyField(TreeCatalog, backref='files')


class DirItem(pw.Model):
    # Dir path.
    location = pw.CharField()

    # Belongs to which TreeCatalog.
    catalog = pw.ForeignKeyField(TreeCatalog, backref='dirs')


class Workload(pw.Model):
    """
    This table stores RISCV workloads packed as a sysroot.
    """
    # Unique name of the workload.
    name = pw.CharField(unique=True, index=True)

    # Optional description.
    description = pw.TextField(null=True)

    # Creation time.
    creation_time = TimestampTzField()

    # Last update time.
    last_update_time = TimestampTzField()


class Sysroot(pw.Model):
    """
    A sysroot containing all the files of a workload, including: workload executables, input data, proxy kernels,
     any other supportive files.
    """

    # Unique name of the sysroot.
    name = pw.CharField(unique=True, index=True)

    # The workload it belongs to
    workload = pw.ForeignKeyField(Workload, backref='sysroots')

    # Optional description.
    description = pw.TextField(null=True)

    # A file catalog of the sysroot tree. Relative to the sysroot folder.
    file_catalog = pw.ForeignKeyField(TreeCatalog)

    # Last update time.
    last_update = TimestampTzField()


class NativeEntry(pw.Model):
    """
    A native entry describe how to launch a workload in the sysroot.
    """
    # The name of the entry.
    name = pw.CharField(unique=True, index=True)

    # Sysroot
    sysroot = pw.ForeignKeyField(Sysroot, backref='native_entries')

    # Command to launch the workload.
    command = pw.CharField()

    # If not null, it indicates the entry requires a file to be redirected to STDIN. Relative to the sysroot.
    stdin_file = pw.CharField()

    # The CWD to run the command. Relative to the sysroot.
    cwd = pw.CharField()

    # The location of the Proxy Kernel. Relative to the sysroot.
    proxy_kernel = pw.CharField()

    # Amount of memory required to launch the workload. In MBytes.
    mem_mb = pw.IntegerField()

    # Optional description.
    description = pw.TextField(null=True)

    # Creation time.
    creation_time = TimestampTzField()

    # Last update time.
    last_update_time = TimestampTzField()


class DecoupledTrace(pw.Model):
    # UUID of the trace.
    uuid = pw.UUIDField(index=True)

    # Which entry produced this trace?
    native_entry = pw.ForeignKeyField(NativeEntry, backref='decoupled_traces')

    # File catalog (relative to the trace folder).
    file_catalog = pw.ForeignKeyField(TreeCatalog)

    # Optional description.
    description = pw.TextField(null=True)

    # Creation time.
    creation_time = TimestampTzField()

    # Last update time.
    last_update_time = TimestampTzField()


class DecoupledCheckpoint(pw.Model):
    # Name of the checkpoint.
    name = pw.CharField()

    # Which traces is required to load this decoupled checkpoint?
    decoupled_trace = pw.ForeignKeyField(DecoupledTrace, backref='decoupled_checkpoints')

    # File catalog (relative to the checkpoint folder).
    file_catalog = pw.ForeignKeyField(TreeCatalog)

    # Optional description.
    description = pw.TextField(null=True)

    # Creation time.
    creation_time = TimestampTzField()

    # Last update time.
    last_update_time = TimestampTzField()
