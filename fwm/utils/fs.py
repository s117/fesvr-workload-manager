import os
import stat
import sys
from typing import Union, Tuple

# shutil and subprocess are imported in the functions that use them: they load many modules, and the read-only
# commands, which load this module, never need them


def remove_path(p: str) -> None:
    if os.path.exists(p):
        if os.path.isfile(p):
            os.remove(p)
        else:
            import shutil
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


def copy_tree(src: str, dst: str) -> None:
    """
    Copy a directory tree, or a file, preserving symlinks as links and the permission bits. dst must not exist.

    The owner and group are not preserved: the copy takes the group of where it goes, e.g. a setgid directory's, so the
    group that shares a repository can read what is copied into it.

    On Linux, GNU cp copies with reflinks where the file system supports them, which makes copying a large tree cheap.
    """
    import shutil
    import subprocess

    if os.path.lexists(dst):
        raise FileExistsError(dst)
    if sys.platform.startswith("linux") and shutil.which("cp"):
        result = subprocess.run(["cp", "-a", "--no-preserve=ownership", "--reflink=auto", "--", src, dst], capture_output=True, text=True,
                                check=False)
        if result.returncode != 0:
            # cp killed (e.g. by a file size limit or the OOM killer) says nothing
            why = result.stderr.strip() or (f"cp was killed by signal {-result.returncode}" if result.returncode < 0
                                             else f"cp exited with status {result.returncode}")
            raise OSError(f"failed to copy {src} to {dst}: {why}")
    elif os.path.isdir(src) and not os.path.islink(src):
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)


def make_movable(path: str) -> None:
    """
    Let a directory be moved to another parent, as an import does with its staged copy: renaming a directory updates
    its "..", which takes write permission on it. Only that directory gets it: the others keep their modes, which the
    import then keeps, but for the write bits it removes.
    """
    if os.path.isdir(path) and not os.path.islink(path):
        os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) | stat.S_IWUSR)


def set_tree_writable(path: str) -> None:
    """
    Give the owner full access to every directory of a tree, so that its entries can be created, renamed or removed,
    and the tree itself can be moved. Files and links are left as they are.
    """
    def make_writable(d: str) -> None:
        os.chmod(d, stat.S_IMODE(os.lstat(d).st_mode) | stat.S_IRWXU)

    make_writable(path)
    for dirpath, dirnames, _ in os.walk(path, followlinks=False):
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if not os.path.islink(full):
                make_writable(full)


def set_tree_readonly(path: str) -> None:
    """
    Remove the write permission bits from every file and directory of a tree (links are left as they are).
    """
    def make_readonly(p: str) -> None:
        os.chmod(p, stat.S_IMODE(os.lstat(p).st_mode) & ~0o222)

    if os.path.isfile(path) and not os.path.islink(path):
        make_readonly(path)
        return
    for dirpath, dirnames, filenames in os.walk(path, topdown=False, followlinks=False):
        for name in filenames + dirnames:
            full = os.path.join(dirpath, name)
            if not os.path.islink(full):
                make_readonly(full)
    make_readonly(path)


def remove_tree(path: str) -> None:
    """
    Remove a file or a directory tree, including the read-only directories in it. A missing path is ignored.
    """
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.remove(path)
        return
    import shutil

    set_tree_writable(path)
    shutil.rmtree(path)
