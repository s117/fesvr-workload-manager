"""
End-to-end test with a real spike: record a run, replay it, create checkpoints, load one.

It runs only when these are set:
  FWM_TEST_SPIKE        the spike binary (built from riscv-isa-sim with FESVR record/replay)
  FWM_TEST_SYSROOT      a sysroot holding a short workload and its boot ELF
  FWM_TEST_ENTRY_POINT  the entry point JSON of the workload in that sysroot

Optionally, FWM_TEST_SPIKE_SRC (riscv-isa-sim's source tree) cross-checks the checkpoint digests with
scripts/checkpoint-digest.py.
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

SPIKE = os.environ.get("FWM_TEST_SPIKE")
SYSROOT = os.environ.get("FWM_TEST_SYSROOT")
ENTRY_POINT = os.environ.get("FWM_TEST_ENTRY_POINT")
SPIKE_SRC = os.environ.get("FWM_TEST_SPIKE_SRC")

pytestmark = pytest.mark.skipif(not (SPIKE and SYSROOT and ENTRY_POINT),
                                reason="set FWM_TEST_SPIKE, FWM_TEST_SYSROOT and FWM_TEST_ENTRY_POINT")


@pytest.fixture
def env(tmp_path):
    e = dict(os.environ)
    e["FWM_REPO_PATH"] = str(tmp_path / "repo")
    e["FWM_SIM_CMD"] = str(SPIKE)
    return e


def fwm(env, *args) -> str:
    return subprocess.run([sys.executable, "-m", "fwm", *args], env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def make(run_dir, *args) -> int:
    return subprocess.run(["make", "-s", "-C", str(run_dir), "run", *args], capture_output=True,
                          check=False).returncode


def instret(run_dir) -> int:
    with open(os.path.join(run_dir, "final-state.json")) as f:
        return json.load(f)["hart_state"][0]["instret"]


def run_json(run_dir) -> dict:
    with open(os.path.join(run_dir, "run.json")) as f:
        return json.load(f)


def fwm_status(env, *args) -> int:
    return subprocess.run([sys.executable, "-m", "fwm", *args], env=env, capture_output=True, check=False).returncode


def test_record_replay_checkpoint(env, tmp_path):
    # a path with a space: every generated Makefile must quote it
    base = tmp_path / "work dir"
    base.mkdir()
    env = {**env, "FWM_REPO_PATH": str(base / "repo")}
    fwm(env, "repo", "init", str(base / "repo"))
    fwm(env, "repo", "add", "sysroot", str(SYSROOT), "sr")
    fwm(env, "repo", "add", "entry", "sr", "wl", "e", str(ENTRY_POINT))
    metadata = {"suite": "Toolchain test", "language": ["C"]}
    (base / "metadata.json").write_text(json.dumps(metadata))
    fwm(env, "repo", "set-metadata", "e", str(base / "metadata.json"))
    fwm(env, "repo", "verify")
    version = base / "sim-version.txt"
    version.write_bytes(subprocess.run([SPIKE, "--version"], check=True, capture_output=True).stdout)

    # record, with a SimPoint profile
    r1 = base / "r1"
    fwm(env, "mkgen", "record", "e", "-o", str(r1))
    assert make(r1, "SIM_FLAGS_EXTRA=-s1000000") == 0
    assert (r1 / "trace" / "boot.elf").is_file()
    assert (r1 / "bbv_proc_0.bb.gz").is_file()
    described = run_json(r1)
    assert (described["kind"], described["sysroot"]["name"], described["entry"]["metadata"]) == ("record", "sr",
                                                                                                  metadata)
    total = instret(r1)
    add_trace = ("repo", "add", "trace", "e", str(r1 / "trace"), "--final-state", str(r1 / "final-state.json"),
                 "--sim-version", str(version))
    trace = fwm(env, *add_trace)
    view = json.loads(fwm(env, "show", "trace", trace, "--json"))
    assert view["complete"]
    assert view["sim_version"]["builds"]["spike"]["Commit"]
    # the same recording is not imported twice
    assert fwm_status(env, *add_trace) == 5
    assert fwm(env, *add_trace, "--exist-ok") == trace

    # replay: the same instruction count
    r2 = base / "r2"
    fwm(env, "mkgen", "replay", trace, "-o", str(r2))
    assert make(r2) == 0
    assert instret(r2) == total
    assert run_json(r2)["trace"]["uuid"] == trace

    # create checkpoints at 1/3 and 2/3 of the run
    desc = base / "d.txt"
    first, second = total // 3, 2 * total // 3
    desc.write_text(f"e.0.0.40: {first}\ne.1.0.60: {second}\n")
    r3 = base / "r3"
    fwm(env, "mkgen", "replay", trace, "--create-checkpoints", str(desc), "-o", str(r3))
    assert make(r3) == 0
    assert sorted(os.listdir(r3 / "checkpoints")) == ["e.0.0.40", "e.1.0.60"]
    # a checkpoint of the description that wasn't created is caught at import
    beyond = base / "d-beyond.txt"
    beyond.write_text(desc.read_text() + f"e.2.0.00: {total * 2}\n")
    assert fwm_status(env, "repo", "add", "checkpoint", trace, str(beyond), str(r3 / "checkpoints")) == 4
    # the first one with its SimPoint, the second one without (other files of the directory are ignored)
    first_desc, second_desc = base / "d-first.txt", base / "d-second.txt"
    first_desc.write_text(f"e.0.0.40: {first}\n")
    second_desc.write_text(f"e.1.0.60: {second}\n")
    info = base / "simpoints.json"
    info.write_text(json.dumps({"interval": 1000000, "maxK": 3, "checkpoints": {"e.0.0.40": {"index": 0,
                                                                                           "weight": 0.4012}}}))
    fwm(env, "repo", "add", "checkpoint", trace, str(first_desc), str(r3 / "checkpoints"),
        "--simpoint-info", str(info), "--sim-version", str(version))
    fwm(env, "repo", "add", "checkpoint", trace, str(second_desc), str(r3 / "checkpoints"))
    ckpts = {c["name"]: c for c in json.loads(fwm(env, "list", "checkpoints", "--json"))}
    assert (ckpts["e.0.0.40"]["simpoint_weight"], ckpts["e.0.0.40"]["simpoint_max_k"]) == (0.4012, 3)
    assert ckpts["e.1.0.60"]["simpoint_weight"] is None
    assert ckpts["e.0.0.40"]["requested_count"] == first
    assert abs(ckpts["e.0.0.40"]["harts"][0]["csr_count"] - first) < 100
    assert abs(ckpts["e.1.0.60"]["harts"][0]["csr_count"] - second) < 100
    if SPIKE_SRC:
        for name, ckpt in ckpts.items():
            digest = subprocess.run([sys.executable, os.path.join(SPIKE_SRC, "scripts", "checkpoint-digest.py"),
                                     str(r3 / "checkpoints" / name)], check=True, capture_output=True).stdout
            assert ckpt["content_sha256"] == hashlib.sha256(digest).hexdigest()

    # validate a checkpoint by running it to the end, then simulate one interval
    r4 = base / "r4"
    fwm(env, "mkgen", "load", "e.0.0.40", "-o", str(r4))
    assert make(r4) == 0
    assert instret(r4) == total
    described = run_json(r4)
    assert (described["kind"], described["trace"]["uuid"], described["checkpoint"]["name"]) == ("load", trace,
                                                                                                 "e.0.0.40")
    assert described["checkpoint"]["simpoint_weight"] == 0.4012
    assert described["entry"]["metadata"] == metadata
    assert fwm(env, "repo", "record-validation", "e.0.0.40", str(r4)).startswith("passed")
    assert make(r4, "SIM_FLAGS_EXTRA=-e1000") == 2  # spike exits 1 when -e stops it, so make fails
    assert fwm_status(env, "repo", "record-validation", "e.0.0.40", str(r4)) == 4

    fwm(env, "repo", "verify")
