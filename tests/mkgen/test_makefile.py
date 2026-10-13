import os
import re
import stat
import subprocess

import pytest

from fwm.mkgen.makefile import (
    generate_record, generate_replay, generate_load, prepare_run_dir, make_escape, MAKEFILE
)
from fwm.repo.checkpoint import add_checkpoint
from fwm.repo.entry import add_entry
from fwm.repo.errors import FwmValidationError, NotFoundError
from fwm.repo.sysroot import add_sysroot
from fwm.repo.trace import add_trace
from tests.fesvr_files import (
    write_recording, write_checkpoint, Command, exit_command, recording_sha256, CheckpointHart
)

COMMANDS = [Command(), exit_command(0)]


def make_dry_run(run_dir, *args) -> str:
    """The commands make would run (make -n), one per line, with single spaces."""
    output = subprocess.run(["make", "-s", "-n", "-C", str(run_dir), "run", *args], check=True, capture_output=True,
                            text=True).stdout
    return re.sub(r"[ \t]+", " ", output.replace("\\\n", " "))


@pytest.fixture
def entry(repo, sysroot_src, write_entry_point):
    add_sysroot(repo, str(sysroot_src), "sr")
    return add_entry(repo, "sr", "wl", "e", write_entry_point(stdin_file="input.txt"))


@pytest.fixture
def trace(repo, entry, tmp_path):
    rec = tmp_path / "rec"
    write_recording(str(rec), harts=[COMMANDS])
    final_state = tmp_path / "final-state.json"
    final_state.write_text('{"hart_state": [{"instret": 5000}]}')
    return add_trace(repo, "e", str(rec), final_state=str(final_state))


def test_record(repo, entry, tmp_path):
    run = tmp_path / "run"
    makefile = generate_record(repo, "e", str(run))
    assert makefile == str(run / MAKEFILE)
    commands = make_dry_run(run)
    assert f"rm -rf {run}/trace {run}/final-state.json {run}/console.stdout {run}/console.stderr" in commands
    assert f"--repo-path {repo.path} spawn --force sr {run}/sysroot" in commands
    sim_line = [line for line in commands.splitlines() if "+chroot=" in line][0]
    assert sim_line.startswith(f"cd {run} && spike -m1 -p1 ")
    for part in (f"+chroot={run}/sysroot", "+target-cwd=/app", f"+dev-traffic-record={run}/trace",
                 f"+final-state-dump={run}/final-state.json", f"+std-dump={run}/console",
                 f"/boot/pk prog.rv64 input.txt < {run}/sysroot/app/input.txt"):
        assert part in sim_line
    # spike options come before FESVR options
    assert sim_line.index("-p1") < sim_line.index("+chroot")


def test_record_overrides(repo, entry, tmp_path):
    run = tmp_path / "run"
    generate_record(repo, "e", str(run))
    commands = make_dry_run(run, "SIM=721sim", "SIM_FLAGS_EXTRA=-e100", "FESVR_FLAGS_EXTRA=+verbose",
                            "BOOT_ELF_FLAGS_EXTRA=-s", "APP_CMD_EXTRA=extra")
    assert f"cd {run} && 721sim -m1 -p1 -e100 +chroot" in commands
    assert "+verbose /boot/pk -s prog.rv64 input.txt extra <" in commands


def test_record_sim_from_env(repo, entry, tmp_path, monkeypatch):
    monkeypatch.setenv("FWM_SIM_CMD", "/opt/spike/bin/spike")
    run = tmp_path / "run"
    generate_record(repo, "e", str(run))
    assert f"cd {run} && /opt/spike/bin/spike -m1" in make_dry_run(run)


def test_record_escapes_command(repo, sysroot_src, write_entry_point, tmp_path):
    add_sysroot(repo, str(sysroot_src), "sr")
    add_entry(repo, "sr", "wl", "e", write_entry_point(command="prog.rv64 '$HOME' '#x' 'a b'"))
    run = tmp_path / "run"
    generate_record(repo, "e", str(run))
    # the shell gets the arguments exactly as in the entry point
    assert "prog.rv64 '$HOME' '#x' 'a b'" in make_dry_run(run)


def test_record_runs(repo, entry, tmp_path):
    """Run the record Makefile with a fake simulator that saves its arguments and its stdin."""
    fake_sim = tmp_path / "fake-sim"
    fake_sim.write_text('#!/bin/sh\necho "$@" > argv.txt\ncat > stdin.txt\n')
    fake_sim.chmod(fake_sim.stat().st_mode | stat.S_IXUSR)
    run = tmp_path / "run"
    generate_record(repo, "e", str(run))
    subprocess.run(["make", "-C", str(run), "run", f"SIM={fake_sim}"], check=True, capture_output=True)
    assert (run / "sysroot" / "app" / "prog.rv64").is_file()
    assert (run / "trace").is_dir()
    assert (run / "stdin.txt").read_text() == "1\n2\n"
    assert "/boot/pk prog.rv64 input.txt" in (run / "argv.txt").read_text()

    # a second run starts from a fresh sysroot
    (run / "sysroot" / "app" / "leftover").write_text("x")
    subprocess.run(["make", "-C", str(run), "run", f"SIM={fake_sim}"], check=True, capture_output=True)
    assert not (run / "sysroot" / "app" / "leftover").exists()


def test_replay(repo, trace, tmp_path):
    run = tmp_path / "run"
    generate_replay(repo, "e", str(run))
    commands = make_dry_run(run)
    assert f"rm -f {run}/final-state.json" in commands
    sim_line = [line for line in commands.splitlines() if "+dev-traffic-replay=" in line][0]
    # no program argument in replay mode
    assert sim_line.strip() == (f"cd {run} && spike -m1 -p1 +dev-traffic-replay={repo.trace_dir(trace.uuid)} "
                                f"+final-state-dump={run}/final-state.json")


def test_create_checkpoints(repo, trace, tmp_path):
    desc = tmp_path / "d.txt"
    desc.write_text("e.0.0.50: 1000\ne.1.0.50: 2000\n")
    run = tmp_path / "run"
    generate_replay(repo, "e", str(run), ckpt_desc=str(desc))
    assert (run / "ckpt.desc").read_text() == desc.read_text()
    commands = make_dry_run(run)
    assert f"rm -rf {run}/final-state.json {run}/checkpoints" in commands
    assert f"mkdir {run}/checkpoints" in commands
    assert (f"cd {run}/checkpoints && spike -m1 -p1 +dev-traffic-replay={repo.trace_dir(trace.uuid)} "
            f"+create-checkpoint={run}/ckpt.desc +final-state-dump={run}/final-state.json") in commands


@pytest.mark.parametrize("text, match", [
    ("a 1000", "has no ':'"),
    ("a: 5000", r"stops after 5000 instructions.*a \(5000\)"),
    ("a: 1\nb: 6000", r"b \(6000\)"),
])
def test_create_checkpoints_invalid(repo, trace, tmp_path, text, match):
    desc = tmp_path / "d.txt"
    desc.write_text(text)
    with pytest.raises(FwmValidationError, match=match):
        generate_replay(repo, "e", str(tmp_path / "run"), ckpt_desc=str(desc))
    assert not (tmp_path / "run").exists()


def test_create_checkpoints_missing_desc(repo, trace, tmp_path):
    with pytest.raises(NotFoundError):
        generate_replay(repo, "e", str(tmp_path / "run"), ckpt_desc=str(tmp_path / "missing"))


def test_load(repo, trace, tmp_path):
    file = tmp_path / "e.0.0.50"
    write_checkpoint(str(file), [CheckpointHart(recording_sha256(COMMANDS))])
    add_checkpoint(repo, "e", str(file))
    run = tmp_path / "run"
    generate_load(repo, "e.0.0.50", str(run))
    commands = make_dry_run(run, "SIM_FLAGS_EXTRA=-e100000000")
    assert (f"cd {run} && spike -m1 -p1 -e100000000 +dev-traffic-replay={repo.trace_dir(trace.uuid)} "
            f"+load-checkpoint={repo.checkpoint_path(trace.uuid, 'e.0.0.50')} "
            f"+final-state-dump={run}/final-state.json") in commands


def test_prepare_run_dir(tmp_path):
    run = tmp_path / "run"
    assert prepare_run_dir(str(run)) == str(run)
    assert run.is_dir()
    # an empty directory, or one generated by fwm, can be reused
    assert prepare_run_dir(str(run)) == str(run)
    (run / MAKEFILE).write_text("# Generated by fwm on today\n")
    (run / "final-state.json").write_text("{}")
    assert prepare_run_dir(str(run)) == str(run)

    other = tmp_path / "other"
    other.mkdir()
    (other / "file").write_text("x")
    with pytest.raises(FwmValidationError, match="not a run directory of fwm"):
        prepare_run_dir(str(other))
    (other / MAKEFILE).write_text("all:\n")
    with pytest.raises(FwmValidationError, match="not a run directory of fwm"):
        prepare_run_dir(str(other))
    with pytest.raises(FwmValidationError, match="not a directory"):
        prepare_run_dir(str(other / "file"))


def test_make_escape():
    assert make_escape("a$b#c") == r"a$$b\#c"


def test_generated_in_place(repo, entry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.mkdir("run")
    monkeypatch.chdir("run")
    generate_record(repo, "e", ".")
    assert (tmp_path / "run" / MAKEFILE).is_file()
