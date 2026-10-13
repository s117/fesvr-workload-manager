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
    return subprocess.run(["make", "-s", "-C", str(run_dir), "run", *args], capture_output=True).returncode


def instret(run_dir) -> int:
    with open(os.path.join(run_dir, "final-state.json")) as f:
        return json.load(f)["hart_state"][0]["instret"]


def test_record_replay_checkpoint(env, tmp_path):
    fwm(env, "repo", "init", str(tmp_path / "repo"))
    fwm(env, "repo", "add", "sysroot", str(SYSROOT), "sr")
    fwm(env, "repo", "add", "entry", "sr", "wl", "e", str(ENTRY_POINT))
    fwm(env, "repo", "verify")

    # record
    r1 = tmp_path / "r1"
    fwm(env, "mkgen", "record", "e", "-o", str(r1))
    assert make(r1) == 0
    assert (r1 / "trace" / "boot.elf").is_file()
    total = instret(r1)
    trace = fwm(env, "repo", "add", "trace", "e", str(r1 / "trace"), "--final-state", str(r1 / "final-state.json"))
    assert json.loads(fwm(env, "show", "trace", trace, "--json"))["complete"]

    # replay: the same instruction count
    r2 = tmp_path / "r2"
    fwm(env, "mkgen", "replay", trace, "-o", str(r2))
    assert make(r2) == 0
    assert instret(r2) == total

    # create checkpoints at 1/3 and 2/3 of the run
    desc = tmp_path / "d.txt"
    first, second = total // 3, 2 * total // 3
    desc.write_text(f"e.0.0.40: {first}\ne.1.0.60: {second}\n")
    r3 = tmp_path / "r3"
    fwm(env, "mkgen", "replay", trace, "--create-checkpoints", str(desc), "-o", str(r3))
    assert make(r3) == 0
    assert sorted(os.listdir(r3 / "checkpoints")) == ["e.0.0.40", "e.1.0.60"]
    for name in ("e.0.0.40", "e.1.0.60"):
        fwm(env, "repo", "add", "checkpoint", trace, str(r3 / "checkpoints" / name))
    ckpts = {c["name"]: c for c in json.loads(fwm(env, "list", "checkpoints", "--json"))}
    assert ckpts["e.0.0.40"]["simpoint_weight"] == 0.4
    assert abs(ckpts["e.0.0.40"]["harts"][0]["csr_count"] - first) < 100
    assert abs(ckpts["e.1.0.60"]["harts"][0]["csr_count"] - second) < 100
    if SPIKE_SRC:
        for name, ckpt in ckpts.items():
            digest = subprocess.run([sys.executable, os.path.join(SPIKE_SRC, "scripts", "checkpoint-digest.py"),
                                     str(r3 / "checkpoints" / name)], check=True, capture_output=True).stdout
            assert ckpt["content_sha256"] == hashlib.sha256(digest).hexdigest()

    # load a checkpoint: run to the end, then one interval
    r4 = tmp_path / "r4"
    fwm(env, "mkgen", "load", "e.0.0.40", "-o", str(r4))
    assert make(r4) == 0
    assert instret(r4) == total
    assert make(r4, "SIM_FLAGS_EXTRA=-e1000") == 2  # spike exits 1 when -e stops it, so make fails

    fwm(env, "repo", "verify")
