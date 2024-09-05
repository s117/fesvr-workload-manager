import hashlib
import os
import shutil
import string
from typing import Union, Tuple

__sha256_cache = {}  # type: dict[str, str]


def sha256_file(fpath: str, use_cache: bool = True) -> str:
    global __sha256_cache
    if use_cache and fpath in __sha256_cache:
        return __sha256_cache[fpath]

    h = hashlib.sha256()
    b = bytearray(128 * 1024)
    mv = memoryview(b)
    with open(fpath, 'rb', buffering=0) as f:
        for n in iter(lambda: f.readinto(mv), 0):
            h.update(mv[:n])
    sha256_hash = h.hexdigest()

    __sha256_cache[fpath] = sha256_hash
    return sha256_hash


def is_valid_sha256(h: str) -> bool:
    return len(h) == 64 and all(_ in string.hexdigits for _ in h)


def remove_path(p: str) -> None:
    if os.path.exists(p):
        if os.path.isfile(p):
            os.remove(p)
        else:
            shutil.rmtree(p)


def get_path_size(path: str) -> int:
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    if os.path.isfile(path):
        total_size = os.path.getsize(path)
    else:
        assert os.path.isdir(path)
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                # skip if it is symbolic link
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)

    return total_size


def human_readable_size(size_bytes: int) -> str:
    def shorten(size: int) -> Tuple[Union[int, float], str]:
        DIV_KB = 1 << 10
        DIV_MB = DIV_KB << 10
        DIV_GB = DIV_MB << 10
        DIV_TB = DIV_GB << 10

        if size < DIV_KB:
            return size, "B"
        elif size < DIV_MB:
            return size / DIV_KB, "K"
        elif size < DIV_GB:
            return size / DIV_MB, "M"
        elif size < DIV_TB:
            return size / DIV_GB, "G"
        else:
            return size / DIV_TB, "T"

    hsize = shorten(size_bytes)
    if isinstance(hsize[0], int):
        str_size = "%d %s" % hsize
    else:
        assert isinstance(hsize[0], float)
        str_size = "%.1f %s" % hsize
    return str_size
