import os
import signal
import subprocess
import sys
import textwrap
import time
import uuid

from click.testing import CliRunner

from fwm.cli.fwm import cli
from fwm.repo.checkpoint import add_checkpoint
from fwm.repo.entry import add_entry
from fwm.repo.gc import collect_garbage, find_leftovers
from fwm.repo.remove import remove_sysroot
from fwm.repo.repo import STAGING_DIR, CHECKPOINTS_DIR, TRACES_DIR
from fwm.repo.sysroot import add_sysroot
from fwm.repo.trace import add_trace
from tests.fesvr_files import write_recording, write_checkpoint, Command, exit_command, recording_sha256, CheckpointHart

COMMANDS = [Command(), exit_command(0)]


def staging_entries(repo):
    return sorted(os.listdir(os.path.join(repo.path, STAGING_DIR)))


def make_old(path):
    old = time.time() - 3600
    os.utime(path, (old, old), follow_symlinks=False)


def kill_import(repo, sysroot_src, tmp_path):
    """Start `fwm repo add sysroot` and SIGKILL it in the middle, as an out-of-memory killer would."""
    ready = tmp_path / "ready"
    script = textwrap.dedent(f"""
        import time
        import fwm.repo.repo as repo_module

        def stuck_create_catalog(*args, **kwargs):
            open({str(ready)!r}, "w").close()
            time.sleep(60)

        repo_module.create_catalog = stuck_create_catalog
        from fwm.cli.fwm import cli
        cli(["--repo-path", {repo.path!r}, "repo", "add", "sysroot", {str(sysroot_src)!r}, "sr"])
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists()
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait()


def test_killed_import(repo, sysroot_src, tmp_path):
    kill_import(repo, sysroot_src, tmp_path)
    left = staging_entries(repo)
    assert len(left) == 2  # the staged copy and its lock

    # too recent to be judged
    assert find_leftovers(repo) == []
    found = find_leftovers(repo, min_age=0)
    assert sorted(os.path.basename(f.path) for f in found) == left
    assert {f.reason for f in found} == {"staging of an import that was interrupted"}

    assert len(collect_garbage(repo, min_age=0)) == 2
    assert staging_entries(repo) == []
    add_sysroot(repo, str(sysroot_src), "sr")


def test_live_import_is_kept(repo):
    with repo.staging() as staged:
        os.mkdir(staged)
        for name in staging_entries(repo):
            make_old(os.path.join(repo.path, STAGING_DIR, name))
        assert find_leftovers(repo, min_age=0) == []


def test_untracked(repo, sysroot_src, write_entry_point, tmp_path, monkeypatch):
    add_sysroot(repo, str(sysroot_src), "sr")
    add_entry(repo, "sr", "wl", "e", write_entry_point())
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    trace = add_trace(repo, "e", str(rec))
    ckpt = tmp_path / "e.0.0.50"
    write_checkpoint(str(ckpt), [CheckpointHart(recording_sha256(COMMANDS))])
    add_checkpoint(repo, "e", str(ckpt))
    assert find_leftovers(repo, min_age=0) == []

    # what interrupted commands can leave: a sysroot removed but not deleted (here: read-only, like all content)...
    add_sysroot(repo, str(sysroot_src), "gone")
    monkeypatch.setattr("fwm.repo.repo.remove_tree", lambda path: None)
    remove_sysroot(repo, "gone")
    monkeypatch.undo()
    # ...items placed by imports that never committed
    stray_trace = os.path.join(repo.path, TRACES_DIR, str(uuid.uuid4()))
    os.mkdir(stray_trace)
    stray_ckpt = os.path.join(repo.checkpoint_dir(trace.uuid), "stray")
    with open(stray_ckpt, "w") as f:
        f.write("x")
    stray_ckpt_dir = os.path.join(repo.path, CHECKPOINTS_DIR, str(uuid.uuid4()))
    os.mkdir(stray_ckpt_dir)

    found = {f.path: f.reason for f in find_leftovers(repo, min_age=0)}
    assert found == {
        repo.sysroot_dir("gone"): "no sysroot of this name",
        stray_trace: "no trace of this UUID",
        stray_ckpt: "no checkpoint of this name",
        stray_ckpt_dir: "no trace of this UUID",
    }
    assert {f.path for f in collect_garbage(repo, min_age=0)} == set(found)
    assert find_leftovers(repo, min_age=0) == []
    assert staging_entries(repo) == []
    # what is tracked is kept
    assert os.path.isdir(repo.sysroot_dir("sr"))
    assert os.path.isfile(repo.checkpoint_path(trace.uuid, "e.0.0.50"))


def test_cli(repo, sysroot_src, tmp_path):
    os.mkdir(repo.sysroot_dir("stray"))
    kill_import(repo, sysroot_src, tmp_path)
    for name in staging_entries(repo):
        make_old(os.path.join(repo.path, STAGING_DIR, name))
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(cli, ["--repo-path", repo.path, *args])
        return result.exit_code, result.stdout

    code, out = run("repo", "verify")
    assert code == 4
    assert out.count("leftover ") == 3

    code, out = run("repo", "gc", "--dry-run")
    assert code == 0
    assert len(out.splitlines()) == 3
    assert os.path.isdir(repo.sysroot_dir("stray"))

    code, out = run("repo", "gc")
    assert code == 0
    assert len(out.splitlines()) == 3
    assert run("repo", "verify")[0] == 0
    assert staging_entries(repo) == []
