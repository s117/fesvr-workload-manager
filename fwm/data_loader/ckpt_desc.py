"""
Parser of checkpoint description files (+create-checkpoint=<file>), a port of ckpt_desc_file_read() in
riscv-fesvr/fesvr/ckpt_desc_reader.cc, so that fwm rejects what FESVR would.

Each non-blank line is "<name>: <instruction count>".
"""
import re
from typing import List, Tuple

# the characters std::isspace() trims in the C locale
_WHITESPACE = "\t\n\v\f\r "
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_COUNT_RE = re.compile(r"^[0-9]+$")
_U64_MAX = (1 << 64) - 1


class CkptDescError(ValueError):
    pass


def parse_ckpt_desc(text: str) -> List[Tuple[str, int]]:
    """
    :return: The (name, instruction count) of each checkpoint, by increasing count.
    :raise CkptDescError: The description is invalid.
    """
    result = []
    for raw_line in text.split("\n"):
        line = raw_line.strip(_WHITESPACE)
        if not line:
            continue
        if ":" not in line:
            raise CkptDescError(f"line '{line}' has no ':' between the checkpoint name and its instruction count")
        name, count = (part.strip(_WHITESPACE) for part in line.rsplit(":", 1))
        if not name:
            raise CkptDescError(f"line '{line}' has an empty checkpoint name")
        if not _COUNT_RE.match(count) or int(count) > _U64_MAX:
            raise CkptDescError(f"line '{line}' has an invalid instruction count")
        result.append((name, int(count)))

    if not result:
        raise CkptDescError("the checkpoint description is empty")
    result.sort(key=lambda c: c[1])

    names = set()
    counts = set()
    for ckpt_name, ckpt_count in result:
        if ckpt_name in names:
            raise CkptDescError(f"two checkpoints are named '{ckpt_name}'")
        if ckpt_count in counts:
            raise CkptDescError(f"two checkpoints are at instruction count {ckpt_count}")
        if not _NAME_RE.match(ckpt_name):
            raise CkptDescError(
                f"invalid checkpoint name '{ckpt_name}': a name may contain only letters, digits, '-', '_' and '.'")
        names.add(ckpt_name)
        counts.add(ckpt_count)
    return result


def read_ckpt_desc(path: str) -> List[Tuple[str, int]]:
    """
    Read and parse a checkpoint description file. Like FESVR, only "\n" ends a line: a file with other line endings
    is read the same way spike reads it.

    :raise OSError: The file can't be read.
    :raise CkptDescError: The description is invalid.
    """
    with open(path, "rb") as f:
        return parse_ckpt_desc(f.read().decode("latin-1"))
