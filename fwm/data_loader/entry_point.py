import json
from typing import Annotated, Any, Optional, Dict, Literal

import pydantic
from annotated_types import Gt


class WorkloadEntry(pydantic.BaseModel):
    """
    An entry point describes how to launch a workload in a sysroot. Its name, description, workload and sysroot are
    given on the command line when the entry point is added; this model holds only the launch fields.

    For all the paths may appear: 1). "/" refers to the sysroot; 2). "." refers to the working directory.
    """
    model_config = pydantic.ConfigDict(extra='forbid')

    # The launch command: the program and its arguments, as one string split with shlex.
    command: str

    # The working directory.
    working_directory: str

    # If not null, the command will be launched with only these environment variables being set.
    envs: Optional[Dict[str, str]]

    # If not null, the file to be redirected to the program as STDIN.
    stdin_file: Optional[str]

    # The ELF the simulator boots, usually the RISCV Proxy Kernel.
    boot_elf: str

    # Amount of memory required to launch the workload. In MBytes.
    mem_mb: Annotated[int, Gt(0)]

    # Number of processors to simulate. Only 1 is supported, as checkpoints can be created for a single hart only.
    nprocs: Literal[1]

    # How `fwm spawn` checks the sysroot out for a run of the program (e.g. a record run): true makes a copy; false
    # makes symbolic links to the repository's read-only files, which is fast, but the program can't open them for
    # writing, and stat() reports them read-only. Like riscv-simenv's copy_spawn.
    copy_spawn: bool = True

    # Optional free-form information about the workload, e.g. {"suite": ..., "description": ..., "language": [...]}.
    # fwm keeps it and shows it, but doesn't interpret it.
    metadata: Optional[Dict[str, Any]] = None


def load_entry_point(path: str) -> WorkloadEntry:
    with open(path, "r") as f:
        entry = json.load(f)

    return WorkloadEntry.model_validate(entry)
