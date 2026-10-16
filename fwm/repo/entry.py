import json
import shlex
from typing import Any, Dict, Optional, Set

import pydantic

from fwm.data_loader.entry_point import load_entry_point, WorkloadEntry
from fwm.data_loader.file_catalog import FileCatalog
from fwm.database.catalog_store import load_catalog
from fwm.database.schema import NativeEntry, Sysroot, Workload
from fwm.repo.errors import AlreadyExistsError, FwmValidationError, NotFoundError
from fwm.repo.repo import Repo, check_name
from fwm.repo.sysroot import get_sysroot
from fwm.utils.log import Logger
from fwm.utils.target_path import normalize_target_path, target_path_to_sysroot_relative


def get_entry(name: str) -> NativeEntry:
    entry = NativeEntry.get_or_none(NativeEntry.name == name)
    if entry is None:
        raise NotFoundError(f"no entry point named '{name}'")
    return entry


def entry_metadata(entry: NativeEntry) -> Optional[Dict[str, Any]]:
    """The entry point's metadata, or None if it has none."""
    return json.loads(entry.metadata) if entry.metadata is not None else None


def _metadata_text(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    return json.dumps(metadata) if metadata is not None else None


def entry_target_cwd(entry: NativeEntry) -> str:
    """The entry's working directory, as a normalized target path (FESVR resolves a relative one against "/")."""
    return normalize_target_path("/", entry.working_directory)


def entry_target_path(entry: NativeEntry, path: str) -> str:
    """A path of the entry (boot ELF, stdin file), as a normalized target path."""
    return normalize_target_path(entry_target_cwd(entry), path)


def _check_paths(entry: WorkloadEntry, catalog: FileCatalog) -> None:
    files: Set[str] = {f.path for f in catalog.files}
    links: Set[str] = {link.path for link in catalog.links}
    dirs: Set[str] = {d.path for d in catalog.dirs}

    cwd = normalize_target_path("/", entry.working_directory)
    cwd_rel = target_path_to_sysroot_relative(cwd)
    if cwd_rel != "." and cwd_rel not in dirs:
        what = "a symlink" if cwd_rel in links else "not a directory of the sysroot"
        raise FwmValidationError(f"working_directory {entry.working_directory} ({cwd}) is {what}")

    def check_file(field: str, path: str) -> None:
        target_path = normalize_target_path(cwd, path)
        rel = target_path_to_sysroot_relative(target_path)
        if rel in links:
            raise FwmValidationError(
                f"{field} {path} ({target_path}) is a symlink. FESVR resolves a link inside the sysroot on the host, "
                f"so it must be a regular file")
        if rel not in files:
            raise FwmValidationError(
                f"{field} {path} ({target_path}) is not a regular file of the sysroot (it is missing, or reached "
                f"through a symlink)")

    check_file("boot_elf", entry.boot_elf)
    if entry.stdin_file is not None:
        check_file("stdin_file", entry.stdin_file)

    # pk opens the program like any other file, relative to the target's working directory
    program = shlex.split(entry.command)[0]
    if target_path_to_sysroot_relative(normalize_target_path(cwd, program)) not in files:
        Logger.get_logger().warning(
            f"the program {program} of the command is not a regular file of the sysroot (relative to the working "
            f"directory {cwd})")


def _existing_entry(existing: NativeEntry, sysroot_name: str, workload_name: str, entry: WorkloadEntry,
                    exist_ok: bool) -> NativeEntry:
    """The answer to adding an entry point under a name already used: that entry point with --exist-ok, if it is the
    same (its description and metadata aside), else an error."""
    what = f"an entry point named '{existing.name}' already exists"
    if not exist_ok:
        raise AlreadyExistsError(what)
    stored = (existing.sysroot.name, existing.workload.name, existing.command, existing.working_directory,
              existing.stdin_file, existing.boot_elf, existing.mem_mb, existing.nprocs, existing.copy_spawn)
    given = (sysroot_name, workload_name, entry.command, entry.working_directory, entry.stdin_file, entry.boot_elf,
             entry.mem_mb, entry.nprocs, entry.copy_spawn)
    if stored != given:
        raise AlreadyExistsError(f"{what}, and differs from this one")
    if entry_metadata(existing) != entry.metadata:
        Logger.get_logger().warning(
            f"entry point '{existing.name}' keeps its metadata, which differs from this one: change it with "
            f"`fwm repo set-metadata`")
    return existing


def add_entry(repo: Repo, sysroot_name: str, workload_name: str, entry_name: str, entry_json: str,
              description: Optional[str] = None, exist_ok: bool = False) -> NativeEntry:
    """
    Add an entry point: how to launch a workload in a sysroot. The paths are checked against the sysroot's catalog.

    :param exist_ok: If the same entry point exists (its description and metadata aside), return it instead of failing.
    :raise AlreadyExistsError: An entry point of this name exists.
    """
    check_name("entry point", entry_name)
    check_name("workload", workload_name)
    try:
        entry = load_entry_point(entry_json)
    except (OSError, json.JSONDecodeError) as e:
        raise FwmValidationError(f"cannot read the entry point {entry_json}: {e}")
    except pydantic.ValidationError as e:
        raise FwmValidationError(f"invalid entry point {entry_json}: {e}")

    if entry.envs:
        raise FwmValidationError(
            "envs is not supported: FESVR passes no environment variable to the target (envp is always empty)")
    try:
        argv = shlex.split(entry.command)
    except ValueError as e:
        raise FwmValidationError(f"cannot split the command '{entry.command}': {e}")
    if not argv:
        raise FwmValidationError("the command is empty")

    sysroot = get_sysroot(sysroot_name)
    _check_paths(entry, load_catalog(sysroot.file_catalog))

    with repo.db.atomic():
        if Sysroot.get_or_none(Sysroot.name == sysroot.name) is None:
            raise NotFoundError(f"sysroot '{sysroot.name}' was removed while adding the entry point")
        existing = NativeEntry.get_or_none(NativeEntry.name == entry_name)
        if existing is not None:
            return _existing_entry(existing, sysroot_name, workload_name, entry, exist_ok)
        workload, _ = Workload.get_or_create(name=workload_name)
        return NativeEntry.create(
            name=entry_name, workload=workload, sysroot=sysroot, command=entry.command,
            working_directory=entry.working_directory, stdin_file=entry.stdin_file, boot_elf=entry.boot_elf,
            mem_mb=entry.mem_mb, nprocs=entry.nprocs, copy_spawn=entry.copy_spawn,
            metadata=_metadata_text(entry.metadata), description=description)


def set_entry_metadata(repo: Repo, entry_name: str, metadata_json: str) -> NativeEntry:
    """
    Replace the metadata of an entry point, keeping its traces.

    :param metadata_json: A file holding a JSON object, or null to remove the metadata.
    :raise FwmValidationError: The file can't be read, or doesn't hold an object or null.
    """
    try:
        with open(metadata_json) as f:
            metadata = json.load(f)
    except (OSError, ValueError) as e:
        raise FwmValidationError(f"cannot read the metadata {metadata_json}: {e}")
    if metadata is not None and not isinstance(metadata, dict):
        raise FwmValidationError(f"the metadata {metadata_json} is not a JSON object, nor null")
    with repo.db.atomic():
        entry = get_entry(entry_name)
        entry.metadata = _metadata_text(metadata)
        entry.save()
    return entry
