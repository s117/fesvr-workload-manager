import json
from typing import Annotated, Optional, List, Dict

import pydantic
from annotated_types import Gt


class WorkloadEntry(pydantic.BaseModel):
    """
    An entry describe how to launch a workload in the sysroot.
    For all the paths may appear: 1). "/" refers to the sysroot; 2). "." refers to the working directory.
    """
    # The entry name.
    name: str

    # The launch command.
    command: str

    # The working directory.
    working_directory: str

    # If not null, the command will be launched with only these environment variables being set.
    envs: Optional[Dict[str, str]]

    # If not null, the file to be redirected to the program as STDIN.
    stdin_file: Optional[str]

    # The location of the RISCV Proxy Kernel.
    proxy_kernel: str

    # Amount of memory required to launch the workload. In MBytes.
    mem_mb: Annotated[int, Gt(0)]

    # Optional description.
    description: Optional[str]


class SysrootMetadata(pydantic.BaseModel):
    # The name of workload in this sysroot.
    workload_name: str

    # The sysroot name.
    sysroot_name: str

    # The entries for all the workloads in this sysroot.
    entry_points: List[WorkloadEntry]


def load_sysroot_metadata(path: str) -> SysrootMetadata:
    with open(path, "r") as f:
        metadata = json.load(f)

    return SysrootMetadata(**metadata)
