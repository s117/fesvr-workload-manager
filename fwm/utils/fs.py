import os
import shutil
from typing import Union, Tuple


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
