import json
import os
import re
import stat
import subprocess

import pytest

from fwm.mkgen.makefile import (
    generate_from_sysroot, generate_from_fesvr_trace, generate_from_checkpoint, generate_from_simpoint, generate_record,
    generate_create_checkpoints, prepare_run_dir, make_escape, shell_value, read_run_info, MAKEFILE
)
from fwm.database.schema import DecoupledTrace
from fwm.mkgen.run_json import RUN_JSON
from fwm.repo import query
from fwm.repo.repo import Repo
from fwm.repo.checkpoint import resolve_checkpoint
from fwm.repo.entry import add_entry, get_entry, set_entry_metadata
from fwm.repo.errors import FwmValidationError, NotFoundError
from fwm.repo.sysroot import add_sysroot, get_sysroot
from fwm.repo.trace import add_trace
from tests.conftest import import_checkpoint
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
    assert f"--repo-path {repo.path} spawn --force e {run}/sysroot" in commands
    sim_line = next(line for line in commands.splitlines() if "+chroot=" in line)
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
    generate_from_fesvr_trace(repo, "e", str(run))
    commands = make_dry_run(run)
    assert f"rm -f {run}/final-state.json" in commands
    sim_line = next(line for line in commands.splitlines() if "+dev-traffic-replay=" in line)
    # no program argument in replay mode
    assert sim_line.strip() == (f"cd {run} && spike -m1 -p1 +dev-traffic-replay={repo.trace_dir(trace.uuid)} "
                                f"+final-state-dump={run}/final-state.json")


def test_create_checkpoints(repo, trace, tmp_path):
    desc = tmp_path / "d.txt"
    desc.write_text("e.0.0.50: 1000\ne.1.0.50: 2000\n")
    run = tmp_path / "run"
    generate_create_checkpoints(repo, "e", str(desc), str(run))
    assert (run / "ckpt.desc").read_text() == desc.read_text()
    commands = make_dry_run(run)
    assert f"rm -rf {run}/final-state.json {run}/checkpoints" in commands
    assert f"mkdir {run}/checkpoints" in commands
    assert (f"cd {run}/checkpoints && spike -m1 -p1 +dev-traffic-replay={repo.trace_dir(trace.uuid)} "
            f"+create-checkpoint={run}/ckpt.desc +final-state-dump={run}/final-state.json") in commands


@pytest.mark.parametrize("text, match", [
    ("a 1000", "has no ':'"),
    ("a: 5000", r"exits after 5000 instructions.*a \(5000\)"),
    ("a: 1\nb: 6000", r"b \(6000\)"),
])
def test_create_checkpoints_invalid(repo, trace, tmp_path, text, match):
    desc = tmp_path / "d.txt"
    desc.write_text(text)
    with pytest.raises(FwmValidationError, match=match):
        generate_create_checkpoints(repo, "e", str(desc), str(tmp_path / "run"))
    assert not (tmp_path / "run").exists()


def test_create_checkpoints_of_several_harts(repo, trace, tmp_path):
    """FESVR creates checkpoints of a single HART only. (No trace has several yet: entry points have one processor.)"""
    DecoupledTrace.update(num_harts=2).where(DecoupledTrace.uuid == trace.uuid).execute()
    desc = tmp_path / "d.txt"
    desc.write_text("c: 10\n")
    with pytest.raises(FwmValidationError, match="has 2 HARTs: FESVR creates checkpoints of a single HART only"):
        generate_create_checkpoints(repo, "e", str(desc), str(tmp_path / "run"))


def test_create_checkpoints_missing_desc(repo, trace, tmp_path):
    with pytest.raises(NotFoundError):
        generate_create_checkpoints(repo, "e", str(tmp_path / "missing"), str(tmp_path / "run"))


def test_from_sysroot(repo, entry, tmp_path):
    run = tmp_path / "run"
    generate_from_sysroot(repo, "e", str(run))
    commands = make_dry_run(run, "SIM_FLAGS_EXTRA=-e100", "APP_CMD_EXTRA=extra")
    assert f"rm -rf {run}/final-state.json {run}/console.stdout {run}/console.stderr" in commands
    assert f"--repo-path {repo.path} spawn --force e {run}/sysroot" in commands
    sim_line = next(line for line in commands.splitlines() if "+chroot=" in line)
    assert sim_line.startswith(f"cd {run} && spike -m1 -p1 -e100 ")
    for part in (f"+chroot={run}/sysroot", "+target-cwd=/app", f"+final-state-dump={run}/final-state.json",
                 f"+std-dump={run}/console", f"/boot/pk prog.rv64 input.txt extra < {run}/sysroot/app/input.txt"):
        assert part in sim_line
    # it records nothing
    assert "dev-traffic" not in commands
    assert "trace" not in commands


SIMPOINT_INFO = {"interval": 100000000, "maxK": 10, "checkpoints": {"c": {"index": 3, "weight": 0.25}}}


def test_from_simpoint(repo, trace, tmp_path):
    file = tmp_path / "c"
    write_checkpoint(str(file), [CheckpointHart(recording_sha256(COMMANDS))])
    import_checkpoint(repo, "e", str(file), simpoint_info=SIMPOINT_INFO)
    run = tmp_path / "run"
    generate_from_simpoint(repo, "c", str(run))
    load = (f"+dev-traffic-replay={repo.trace_dir(trace.uuid)} "
            f"+load-checkpoint={repo.checkpoint_path(trace.uuid, 'c')} +final-state-dump={run}/final-state.json")
    assert f"cd {run} && spike -m1 -p1 -e100000000 {load}" in make_dry_run(run)
    # the interval stays when other spike options are given, and INTERVAL changes it
    assert f"spike -m1 -p1 -e100000000 -l {load}" in make_dry_run(run, "SIM_FLAGS_EXTRA=-l")
    assert f"spike -m1 -p1 -e5000 {load}" in make_dry_run(run, "INTERVAL=5000")
    with open(run / MAKEFILE) as f:
        assert "at SimPoint 3 of\n# weight 0.25" in f.read()


def test_from_simpoint_needs_simpoint_info(repo, trace, tmp_path):
    file = tmp_path / "c"
    write_checkpoint(str(file), [CheckpointHart(recording_sha256(COMMANDS))])
    import_checkpoint(repo, "e", str(file))
    with pytest.raises(FwmValidationError, match="has no SimPoint information.*from-checkpoint"):
        generate_from_simpoint(repo, "c", str(tmp_path / "run"))
    assert not (tmp_path / "run").exists()


def test_from_checkpoint(repo, trace, tmp_path):
    file = tmp_path / "e.0.0.50"
    write_checkpoint(str(file), [CheckpointHart(recording_sha256(COMMANDS))])
    import_checkpoint(repo, "e", str(file))
    run = tmp_path / "run"
    generate_from_checkpoint(repo, "e.0.0.50", str(run))
    commands = make_dry_run(run, "SIM_FLAGS_EXTRA=-e100000000")
    assert (f"cd {run} && spike -m1 -p1 -e100000000 +dev-traffic-replay={repo.trace_dir(trace.uuid)} "
            f"+load-checkpoint={repo.checkpoint_path(trace.uuid, 'e.0.0.50')} "
            f"+final-state-dump={run}/final-state.json") in commands


def test_prepare_run_dir(tmp_path):
    run = tmp_path / "run"
    assert prepare_run_dir(str(run), "record") == str(run)
    assert run.is_dir()
    # an empty directory, or one generated by fwm for the same kind of run, can be reused
    assert prepare_run_dir(str(run), "record") == str(run)
    (run / MAKEFILE).write_text('# Generated by fwm on today\n#   fwm ...\n'
                                '# fwm-run: {"kind": "record", "entry": "e"}\n')
    (run / "final-state.json").write_text("{}")
    assert prepare_run_dir(str(run), "record") == str(run)

    other = tmp_path / "other"
    other.mkdir()
    (other / "file").write_text("x")
    with pytest.raises(FwmValidationError, match="not a run directory of fwm"):
        prepare_run_dir(str(other), "record")
    (other / MAKEFILE).write_text("all:\n")
    with pytest.raises(FwmValidationError, match="not a run directory of fwm"):
        prepare_run_dir(str(other), "record")
    with pytest.raises(FwmValidationError, match="not a directory"):
        prepare_run_dir(str(other / "file"), "record")


def test_run_dir_of_another_kind(repo, trace, tmp_path):
    """A run directory isn't reused for another kind of run, whose Makefile wouldn't remove the former outputs."""
    run = tmp_path / "run"
    generate_record(repo, "e", str(run))
    (run / "trace").mkdir()  # what the record run left
    with pytest.raises(FwmValidationError, match="is the run directory of a record run, whose outputs this "
                                                 "from-sysroot run wouldn't remove"):
        generate_from_sysroot(repo, "e", str(run))
    assert read_run_info(str(run)) == {"kind": "record", "entry": "e"}
    # the same kind of run, of another subject or not, reuses it
    generate_record(repo, "e", str(run))
    generate_from_fesvr_trace(repo, "e", str(tmp_path / "replay"))
    generate_from_fesvr_trace(repo, str(trace.uuid), str(tmp_path / "replay"))
    # a Makefile of fwm without the run information
    (run / MAKEFILE).write_text("# Generated by fwm on today\n")
    with pytest.raises(FwmValidationError, match="of a former run"):
        generate_record(repo, "e", str(run))


def test_make_escape():
    assert make_escape("a$b#c") == r"a$$b\#c"


def test_generated_in_place(repo, entry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.mkdir("run")
    monkeypatch.chdir("run")
    generate_record(repo, "e", ".")
    assert (tmp_path / "run" / MAKEFILE).is_file()


AWKWARD = "a b;c $HOME 'q' #x"


def fake_sim(tmp_path):
    """A simulator that saves its arguments (one per line) and its stdin in the current directory."""
    sim = tmp_path / "fake-sim"
    sim.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > argv.txt\ncat > stdin.txt 2>/dev/null\n')
    sim.chmod(sim.stat().st_mode | stat.S_IXUSR)
    return sim


def test_awkward_paths(tmp_path, sysroot_src, write_entry_point):
    """Paths with spaces and shell or make metacharacters reach the simulator intact."""
    base = tmp_path / AWKWARD
    base.mkdir()
    repo = Repo.init(str(base / "repo"))
    try:
        add_sysroot(repo, str(sysroot_src), "sr")
        add_entry(repo, "sr", "wl", "e", write_entry_point(stdin_file="input.txt"))
        sim = fake_sim(tmp_path)

        run = base / "rec run"
        generate_record(repo, "e", str(run))
        subprocess.run(["make", "-C", str(run), "run", f"SIM={sim}"], check=True, capture_output=True)
        argv = (run / "argv.txt").read_text().splitlines()
        assert f"+chroot={run}/sysroot" in argv
        assert f"+dev-traffic-record={run}/trace" in argv
        assert f"+final-state-dump={run}/final-state.json" in argv
        assert argv[-3:] == ["/boot/pk", "prog.rv64", "input.txt"]
        assert (run / "stdin.txt").read_text() == "1\n2\n"
        assert (run / "trace").is_dir()

        rec = tmp_path / "rec"
        write_recording(str(rec), harts=[COMMANDS])
        trace = add_trace(repo, "e", str(rec))
        run = base / "replay run"
        generate_from_fesvr_trace(repo, "e", str(run))
        (run / "final-state.json").write_text("stale")
        subprocess.run(["make", "-C", str(run), "run", f"SIM={sim}"], check=True, capture_output=True)
        argv = (run / "argv.txt").read_text().splitlines()
        assert f"+dev-traffic-replay={repo.trace_dir(trace.uuid)}" in argv
        assert not (run / "final-state.json").exists()
    finally:
        repo.close()


def test_shell_value():
    assert shell_value("/plain/path") == "/plain/path"
    assert shell_value("a b") == "'a b'"
    assert shell_value("$x#y") == r"'$$x\#y'"
    with pytest.raises(FwmValidationError, match="newline"):
        shell_value("a\nb")


def test_run_dir_with_newline(tmp_path):
    with pytest.raises(FwmValidationError, match="newline"):
        prepare_run_dir(str(tmp_path / "a\nb"), "record")
    assert os.listdir(tmp_path) == []


def test_run_info(repo, trace, tmp_path):
    generate_record(repo, "e", str(tmp_path / "record"))
    assert read_run_info(str(tmp_path / "record")) == {"kind": "record", "entry": "e"}
    generate_from_fesvr_trace(repo, "e", str(tmp_path / "replay"))
    assert read_run_info(str(tmp_path / "replay")) == {"kind": "from-fesvr-trace", "trace": str(trace.uuid)}
    desc = tmp_path / "d.txt"
    desc.write_text("c: 10\n")
    generate_create_checkpoints(repo, "e", str(desc), str(tmp_path / "create"))
    assert read_run_info(str(tmp_path / "create")) == {"kind": "create-checkpoints", "trace": str(trace.uuid)}
    generate_from_sysroot(repo, "e", str(tmp_path / "sysroot"))
    assert read_run_info(str(tmp_path / "sysroot")) == {"kind": "from-sysroot", "entry": "e"}
    file = tmp_path / "c"
    write_checkpoint(str(file), [CheckpointHart(recording_sha256(COMMANDS))])
    import_checkpoint(repo, "e", str(file), simpoint_info=SIMPOINT_INFO)
    for kind in ("from-checkpoint", "from-simpoint"):
        generate = generate_from_checkpoint if kind == "from-checkpoint" else generate_from_simpoint
        generate(repo, "c", str(tmp_path / kind))
        assert read_run_info(str(tmp_path / kind)) == {"kind": kind, "trace": str(trace.uuid), "checkpoint": "c"}
    assert read_run_info(str(tmp_path)) is None


def read_run_json(run_dir) -> dict:
    with open(os.path.join(run_dir, RUN_JSON)) as f:
        return json.load(f)


def test_run_json(repo, trace, tmp_path, monkeypatch):
    """run.json holds the views of `fwm show --json` of what each run uses, without their lists of other items."""
    monkeypatch.setenv("FWM_SIM_CMD", "/opt/spike")
    metadata = tmp_path / "metadata.json"
    metadata.write_text('{"suite": "GAPBS", "language": ["C++"]}')
    set_entry_metadata(repo, "e", str(metadata))
    file = tmp_path / "c"
    write_checkpoint(str(file), [CheckpointHart(recording_sha256(COMMANDS))])
    import_checkpoint(repo, "e", str(file), simpoint_info=SIMPOINT_INFO)
    desc = tmp_path / "d.txt"
    desc.write_text("a: 10\nb: 20\n")

    entry = get_entry("e")
    sysroot = {k: v for k, v in query.sysroot_view(repo, get_sysroot("sr")).items() if k != "entries"}
    workload = {k: v for k, v in query.workload_view(entry.workload).items() if k != "entries"}
    entry_view = {k: v for k, v in query.entry_view(entry).items() if k != "traces"}
    trace_view = {k: v for k, v in query.trace_view(repo, trace).items() if k != "checkpoints"}
    ckpt_view = query.checkpoint_view(repo, resolve_checkpoint("c"))
    assert entry_view["metadata"] == {"suite": "GAPBS", "language": ["C++"]}
    common = {"sysroot": sysroot, "workload": workload, "entry": entry_view}

    generate_from_sysroot(repo, "e", str(tmp_path / "from-sysroot"))
    generate_from_fesvr_trace(repo, "e", str(tmp_path / "from-fesvr-trace"))
    generate_from_checkpoint(repo, "c", str(tmp_path / "from-checkpoint"))
    generate_from_simpoint(repo, "c", str(tmp_path / "from-simpoint"))
    generate_record(repo, "e", str(tmp_path / "record"))
    generate_create_checkpoints(repo, "e", str(desc), str(tmp_path / "create-checkpoints"))
    assert ckpt_view["simpoint_interval"] == 100000000
    expected = {
        "from-sysroot": {"kind": "from-sysroot", **common},
        "from-fesvr-trace": {"kind": "from-fesvr-trace", **common, "trace": trace_view},
        "from-checkpoint": {"kind": "from-checkpoint", **common, "trace": trace_view, "checkpoint": ckpt_view},
        "from-simpoint": {"kind": "from-simpoint", **common, "trace": trace_view, "checkpoint": ckpt_view},
        "record": {"kind": "record", **common},
        "create-checkpoints": {"kind": "create-checkpoints", **common, "trace": trace_view,
                               "requested_checkpoints": [{"name": "a", "count": 10}, {"name": "b", "count": 20}]},
    }
    for run, sections in expected.items():
        described = read_run_json(tmp_path / run)
        generated = described.pop("generated")
        assert described == sections, run
        assert list(described) == list(sections), run  # in this order
        assert set(generated) == {"time", "command", "fwm_version", "repo", "sim"}
        assert (generated["repo"], generated["sim"]) == (repo.path, "/opt/spike")
        # the Makefile says when and how it was generated, like run.json
        with open(tmp_path / run / MAKEFILE) as f:
            assert generated["time"] in f.readline()
            assert f.readline().strip() == "#   " + generated["command"]


def test_run_json_regenerated(repo, entry, tmp_path):
    """Generating again in a run directory replaces run.json, and running the Makefile leaves it alone."""
    run = tmp_path / "run"
    generate_record(repo, "e", str(run))
    assert read_run_json(run)["entry"]["metadata"] is None
    metadata = tmp_path / "metadata.json"
    metadata.write_text('{"suite": "x"}')
    set_entry_metadata(repo, "e", str(metadata))
    generate_record(repo, "e", str(run))
    assert read_run_json(run)["entry"]["metadata"] == {"suite": "x"}
    assert RUN_JSON not in make_dry_run(run)
