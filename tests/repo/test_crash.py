"""
Failures and interruptions in the middle of an import or a removal: the repository must stay consistent.
"""
import fcntl
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from fwm.database.schema import Sysroot
from fwm.repo.remove import remove_sysroot
from fwm.repo.repo import STAGING_DIR, STAGING_LOCK_SUFFIX
from fwm.repo.sysroot import add_sysroot


def staging_entries(repo):
    return os.listdir(os.path.join(repo.path, STAGING_DIR))


def test_failed_commit_leaves_nothing(repo, sysroot_src, monkeypatch):
    def failing_commit(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(repo.db, "commit", failing_commit)
        with pytest.raises(OSError, match="disk full"):
            add_sysroot(repo, str(sysroot_src), "sr")
    assert Sysroot.select().count() == 0
    assert not os.path.lexists(repo.sysroot_dir("sr"))
    assert staging_entries(repo) == []
    # and the name can be used
    add_sysroot(repo, str(sysroot_src), "sr")


def test_failed_removal_commit_keeps_files(repo, sysroot_src, monkeypatch):
    add_sysroot(repo, str(sysroot_src), "sr")

    def failing_commit(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(repo.db, "commit", failing_commit)
        with pytest.raises(OSError):
            remove_sysroot(repo, "sr")
    assert Sysroot.select().count() == 1
    assert os.path.isdir(repo.sysroot_dir("sr"))


def test_removal_deletes_after_commit(repo, sysroot_src, monkeypatch, caplog):
    """A file that can't be deleted after the commit is reported, not a failure: the rows are gone."""
    add_sysroot(repo, str(sysroot_src), "sr")

    def failing_remove_tree(path):
        raise PermissionError("busy")

    monkeypatch.setattr("fwm.repo.repo.remove_tree", failing_remove_tree)
    remove_sysroot(repo, "sr")
    assert Sysroot.select().count() == 0
    assert os.path.isdir(repo.sysroot_dir("sr"))  # left for fwm repo gc
    assert "fwm repo gc" in caplog.text


def test_staging_holds_a_lock(repo):
    with repo.staging() as staged:
        lock = staged + STAGING_LOCK_SUFFIX
        assert os.path.isfile(lock)
        fd = os.open(lock, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        os.mkdir(staged)
    assert staging_entries(repo) == []


def test_sigterm_during_import_cleans_up(repo, sysroot_src, tmp_path):
    """A job stopped by its scheduler (SIGTERM) in the middle of an import leaves nothing behind."""
    ready = tmp_path / "ready"
    script = textwrap.dedent(f"""
        import time
        import fwm.utils.file_catalog as catalog_module

        create_catalog = catalog_module.create_catalog

        def slow_create_catalog(*args, **kwargs):
            open({str(ready)!r}, "w").close()
            time.sleep(60)
            return create_catalog(*args, **kwargs)

        catalog_module.create_catalog = slow_create_catalog
        from fwm.cli.fwm import cli
        cli(["--repo-path", {repo.path!r}, "repo", "add", "sysroot", {str(sysroot_src)!r}, "sr"])
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(200):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists(), proc.stderr.read() if proc.poll() is not None else "the import didn't start"
        assert staging_entries(repo) != []
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 128 + signal.SIGTERM
    finally:
        proc.kill()
        proc.wait()
    assert staging_entries(repo) == []
    assert not os.path.lexists(repo.sysroot_dir("sr"))
