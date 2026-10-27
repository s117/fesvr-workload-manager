---
name: use-fwm
description: Run RISC-V simulations from an fwm (FESVR Workload Manager) repository, a directory with fwm.db, often given by FWM_REPO_PATH. Use when asked to run, launch, simulate or benchmark a RISC-V workload from an fwm repository; to find or search its workloads, entry points, traces or checkpoints; to read their metadata; to simulate SimPoint intervals or sweep a workload's checkpoints; or to run them on spike or a spike-compatible simulator such as 721sim, including with a spike extension or a 721sim pipeline configuration.
---

# Running simulations from an fwm repository

This skill is for the **users** of an fwm repository: they find workloads, read their metadata, and simulate them. They may have read access only, and need nothing more. Creating or filling a repository is another job (see the end).

## Why fwm exists

- **Traces.** A spike whose front-end server (FESVR) can record a run's device traffic: every system call the program makes, with the memory it reads and writes. The recording is a **trace**.
- **Replays.** A replay of a trace needs no sysroot and no host files. It runs on any host, and it prints nothing: the program's output existed only in the recorded run.
- **Checkpoints** are snapshots taken while replaying a trace. A checkpoint loads on top of its trace's replay, so restoring one costs the same however deep in the run it is.
- **SimPoint.** Some checkpoints mark SimPoint intervals: a few short intervals that, weighted, represent the whole run. Simulating only them estimates the whole program in a fraction of the time.
- **fwm** keeps all of this in a repository, and generates the Makefiles that run each kind of simulation.

| Term | Meaning |
| --- | --- |
| **sysroot** | A directory tree the program sees as `/`. |
| **workload** | A name grouping the entry points that run the same program. |
| **entry point** | How to launch a workload in a sysroot: command, working directory, boot ELF, memory size, and free-form metadata. |
| **trace** | A recording of a run of an entry point, named by a UUID. |
| **checkpoint** | A snapshot taken while replaying a trace. It loads only with that trace. A SimPoint checkpoint also has a weight, an index and an interval size. |

What follows for a user:
- a checkpoint belongs to one trace, and a trace to one entry point;
- only a `from-sysroot` run shows the program's console output; replays and checkpoint runs print nothing of the program's own;
- every command here only reads the repository.

## Before starting

- **The repository.** fwm finds it with `--repo-path DIR` (before the subcommand: `fwm --repo-path DIR list`) or the `FWM_REPO_PATH` environment variable. *Never guess its location: ask the user.* A repository is a directory holding `fwm.db`.
- **fwm on `PATH`.** `fwm --version` says which build you have. If `fwm` is missing, ask the user how fwm is installed where they work (e.g. `uv tool install` from its source).
- **The simulator.** `SIM=/path/to/simulator` on make's command line, or `FWM_SIM_CMD` when generating; the default is `spike` on `PATH`.
  - **spike** is functional: it executes the ISA, with no timing. Use it to run programs and count instructions.
  - **721sim** is a spike-compatible timing simulator: cycles, IPC and caches come from it.
  - *If the user didn't say which simulator, ask.*

```bash
export FWM_REPO_PATH=/path/to/repo
fwm --version
```

## Find workloads: search with `fwm list`

`fwm list [KIND]` prints the names of one kind, one per line: `workloads`, `sysroots`, `entries` (the default), `traces` or `checkpoints`. `--json` prints a JSON array of the names.

Filters keep what is related to an entity they match, directly or through others (workload/sysroot → entry point → trace → checkpoint):
- `--workload`, `--sysroot`, `--entry`, `--trace`, `--checkpoint GLOB`: shell-style globs, case-sensitive. **Quote them.**
- `--simpoint`: only what is related to a checkpoint with SimPoint information.
- The same filter twice means either; different filters must all hold.
- A glob that matches nothing prints a warning, and the command still succeeds.

```bash
# Which entry points run workload phases?
fwm list entries --workload phases
phases-n100k
phases-n300k

# The checkpoints of an entry point
fwm list checkpoints --entry phases-n100k
8311b345-ac8f-468b-b5cb-79d604191e7c:mid
phases-n100k.3
phases-n100k.8
phases-n100k.12

# Those with SimPoint information: what `fwm mkgen from-simpoint` can simulate
fwm list checkpoints --entry phases-n100k --simpoint
phases-n100k.3
phases-n100k.8
phases-n100k.12

# The sysroot where some checkpoints were made
fwm list sysroots --checkpoint 'phases-n300k.*'
demo-rootfs
```

**How names are given:**
- **A trace** is its UUID, a unique prefix of it, or the name of an entry point that has a single trace.
- **A checkpoint** is its name when no other trace has a checkpoint of that name. Otherwise it is `TRACE:NAME`, where TRACE is any of the forms above: e.g. `phases-n100k:mid` for the `mid` of that entry point's trace. `fwm list` prints each checkpoint the way the other commands accept it: above, two traces have a checkpoint named `mid`.
- **Names are just names.** A checkpoint's SimPoint data comes from its metadata, never from its name.

## Read metadata: `fwm show`

`fwm show KIND NAME` prints the details of one workload, sysroot, entry, trace or checkpoint; `--json` prints them as JSON. Pick fields with Python, which fwm's host always has:

```bash
fwm show checkpoint phases-n100k.8 --json |
  python3 -c 'import json, sys; c = json.load(sys.stdin); print(c["simpoint_weight"], c["simpoint_interval"])'
0.45 100000

# Which simulator build recorded the trace of an entry point (it has one trace)
fwm show trace phases-n100k --json |
  python3 -c 'import json, sys; b = json.load(sys.stdin)["provenance"]["simulator"]["build"]; print(b["version"], b["commit"])'
```

The fields that matter:
- **entry**:
  - `workload`, `sysroot`, `command`, `working_directory`, `boot_elf`, `mem_mb`;
  - `metadata`: free-form, e.g. `{"suite": ..., "description": ..., "language": [...]}`;
  - `traces`: each `{"uuid", "checkpoints"}`.
- **trace**:
  - `complete` (the program exited) and `exit_status`;
  - `harts[].instret`: the run's total instruction count;
  - `checkpoints`;
  - `provenance`: when and where it was recorded, the command line, and the simulator's and FESVR's builds (`provenance.simulator.build.version`, a `git describe` string, and `.commit`).
- **checkpoint**:
  - `trace` and `entry`;
  - `simpoint_weight`, `simpoint_index`, `simpoint_interval`, `simpoint_max_k`: all `null` without SimPoint information;
  - `requested_count`: the instruction count where it was taken;
  - `validation`: `null`, or `{"status": "passed" | "failed", "detail": ...}` after a run from it to the program's end was checked.

A checkpoint, trimmed:

```json
{
  "name": "phases-n100k.8",
  "trace": "8311b345-ac8f-468b-b5cb-79d604191e7c",
  "entry": "phases-n100k",
  "simpoint_weight": 0.45,
  "simpoint_index": 8,
  "simpoint_interval": 100000,
  "simpoint_max_k": 5,
  "requested_count": 800000,
  "validation": {"status": "passed", "detail": "the program exited at the trace's instret [1503320], with its exit code 0"}
}
```

**`run.json`.** Every run directory `fwm mkgen` generates holds a `run.json`: a snapshot of what the run uses, taken when generating. Its keys are `kind` (e.g. `from-simpoint`), `generated` (when, by which command), and `sysroot`, `workload`, `entry`, `trace` and `checkpoint`, as `fwm show --json` gives them. So a run's results can be read with their metadata later, without the repository:

```bash
python3 -c 'import json; print(json.load(open("run1/run.json"))["checkpoint"]["simpoint_weight"])'
0.45
```

## Launch a simulation: `fwm mkgen`, then `make`

`fwm mkgen KIND NAME -o DIR` writes a run directory (a `Makefile` and `run.json`). `make -C DIR run` runs it. **Always pass `-o`:** the default is the current directory.

| Command | Starts | Needs | Writes |
| --- | --- | --- | --- |
| `from-sysroot ENTRY` | the program, for real, in a fresh copy of its sysroot | a Linux host, and fwm when running | the console (printed, and in `console.stdout` / `console.stderr`), and the program's files in `DIR/sysroot/` |
| `from-fesvr-trace TRACE` | a replay from the start | the trace only; any host | nothing of the program's |
| `from-checkpoint CKPT` | at the checkpoint, to the program's end | its trace; any host | nothing of the program's |
| `from-simpoint CKPT` | at a SimPoint checkpoint, for its interval | a checkpoint with SimPoint information | nothing of the program's |

Every run writes `final-state.json` (see below).

```bash
fwm mkgen from-simpoint phases-n100k.8 -o run1
make -C run1 run SIM=/path/to/simulator
```

**Variables on make's command line:**
- `SIM`: the simulator.
- `SIM_FLAGS_EXTRA`: the simulator's own options (below). Never `-m` or `-p`: the Makefile sets them from the entry point, and a replay needs those exact values.
- `FESVR_FLAGS_EXTRA`: FESVR options, e.g. `+verbose`.

**The instruction limit, `-e<n>`:**
- **A `from-simpoint` run sets it itself:** its Makefile passes `-e$(INTERVAL)`, with `INTERVAL` set to the checkpoint's interval size when generating. Never add `-e`, nor set `INTERVAL`, to simulate a SimPoint interval.
- **Other runs go to the program's end,** unless `SIM_FLAGS_EXTRA=-e<n>` stops them after n instructions.
- **`-e` counts from where the run starts** (after the checkpoint's restore). `final-state.json`'s `instret` counts from the program's start. E.g. a checkpoint at 800000 with an interval of 100000 ends at `instret` 900000.

**The simulator's options, in `SIM_FLAGS_EXTRA`.** `<simulator> -h` lists them. The user knows which options, extensions and configurations they want: ask when it isn't clear.

- **spike, loading an extension** (a tracer or profiler built as a shared library):
  ```bash
  make -C run1 run SIM=/path/to/spike \
      SIM_FLAGS_EXTRA="--extlib=/abs/path/to/libEXT.so --extension=EXT"
  ```
  - EXT is the name the extension registers itself under.
  - **The path must be absolute:** the Makefile runs the simulator from the run directory. A relative one fails with `Unable to load extlib ... cannot open shared object file`.
  - **Build the library against the same spike as `SIM`.** A library built for another spike build can crash it, e.g. with a segmentation fault; then `final-state.json` says `unfinished`.
  - **Environment variables:** if an extension takes settings from them, set them for make (`VAR=value make -C run1 run ...`): the simulator inherits them.
  - **Outputs:** the extension's output files land in the run directory.
- **721sim:** its pipeline configuration flags go in `SIM_FLAGS_EXTRA` (`721sim -h` lists them). Its statistics files land in the run directory.

**Run directories.**
- **Use a new directory per run.** Running `make` again reruns it: the Makefile first removes that run's own outputs.
- **A directory can be generated again only for the same kind of run.** fwm refuses another kind, whose Makefile wouldn't remove the former outputs.
- **What the Makefile leaves:** files written by the simulator or an extension (statistics, logs) are not removed by the next run. Use a fresh directory.

`fwm spawn ENTRY DEST` makes a working copy of an entry point's sysroot, e.g. to look at the program's input files.

## Judge a run by `final-state.json`, never by make's exit status

The exit statuses mislead:
- spike exits 1 when `-e` stops it, so for a normal `from-simpoint` run make reports `Error 1` and itself exits 2;
- 721sim exits 0 then;
- a program that legitimately exits non-zero fails make;
- a simulator stopped by a job's time limit (SIGTERM) exits 0.

`final-state.json` says why the run ended:

```json
{"finish_reason": "instret_limit", "hart_state": [{"instret": 900000}]}
```

| `finish_reason` | Meaning |
| --- | --- |
| `target_exit` | The program exited, with `exit_code`. Normal for `from-sysroot`, `from-fesvr-trace` and `from-checkpoint`. |
| `instret_limit` | `-e` stopped it. Normal for `from-simpoint`. |
| `interrupted` | SIGTERM or SIGINT (in `signal`) cut it short, e.g. a job's time limit: rerun with more time. |
| `error` | The FESVR failed, with its message in `error`, e.g. a replay that diverged from its recording. |
| `unfinished` | The run is still going, or the simulator died (e.g. a crash, or `kill -9`). |

`hart_state[].instret` is each processor's instruction count at the end, counted from the program's start. `error` and `unfinished` have no `hart_state`.

**A run ended correctly when:**
- **`from-simpoint`:** `instret_limit`, and `instret` = the checkpoint's `requested_count` + `simpoint_interval`;
- **`from-fesvr-trace` and `from-checkpoint`** (replays, to the program's end): `target_exit`, with `instret` and `exit_code` equal to the trace's `harts[].instret` and `exit_status`. Anything else means the run diverged from its recording.
- **`from-sysroot`:** `target_exit`, with the `exit_code` you expect (usually the trace's `exit_status`). Don't expect its `instret` to equal the trace's. A real run's system calls are answered by the host, and the program's path can depend on their answers. For example, with the program's stdout going to `/dev/null` rather than a file, a run took about 200 more instructions out of 1.5 million, with the same output.

```bash
python3 -c 'import json; s = json.load(open("run1/final-state.json")); print(s["finish_reason"], s.get("hart_state"))'
instret_limit [{'instret': 900000}]
```

## Sweep a workload's SimPoint checkpoints

Simulate every SimPoint interval of an entry point, then combine the results by weight.

```bash
SIM=/path/to/simulator
E=phases-n100k                     # the entry point
OUT=sweep/$E                       # one run directory per checkpoint, under it
for ck in $(fwm list checkpoints --entry "$E" --simpoint); do
  d="$OUT/${ck//:/_}"              # a TRACE:NAME reference makes a TRACE_NAME directory
  fwm mkgen from-simpoint "$ck" -o "$d" > /dev/null || { echo "mkgen failed: $ck"; continue; }  # its "run it with" hint still goes to stderr
  make -C "$d" run SIM="$SIM" > "$d.log" 2>&1   # its exit status means nothing: judge by final-state.json
done
```

Each run is independent: on a cluster, submit each `make -C DIR run` as its own job.

Then check every run, and collect each checkpoint's weight from the `run.json` snapshot. Here the metric is the number of instructions each run simulated: replace it with the one you need, read from the simulator's statistics in the run directory.

```bash
python3 - sweep/phases-n100k/*/ <<'EOF'
import json, sys
from pathlib import Path

total_w = weighted = 0.0
for d in map(Path, sys.argv[1:]):
    state = json.loads((d / "final-state.json").read_text())
    ckpt = json.loads((d / "run.json").read_text())["checkpoint"]
    w = ckpt["simpoint_weight"]
    ok = (state["finish_reason"] == "instret_limit" and
          state["hart_state"][0]["instret"] == ckpt["requested_count"] + ckpt["simpoint_interval"])
    if not ok:
        print(f'{ckpt["name"]}: {state["finish_reason"]}  <- rerun')
        continue
    metric = state["hart_state"][0]["instret"] - ckpt["requested_count"]   # the metric M of this run
    total_w += w
    weighted += w * metric
    print(f'{ckpt["name"]:20} weight {w:<6} M {metric}')
print("sum of weights:", round(total_w, 6), " weighted mean of M:", weighted / total_w)
EOF
phases-n100k.12      weight 0.25   M 100000
phases-n100k.3       weight 0.3    M 100000
phases-n100k.8       weight 0.45   M 100000
sum of weights: 1.0  weighted mean of M: 100000.0
```

**Combining the results.** Each run i has a weight wᵢ and a metric Mᵢ, read from the simulator's statistics in its run directory.
- **Weights come from `run.json`** (`.checkpoint.simpoint_weight`), never from checkpoint names.
- **Check that they sum to about 1.** Less means checkpoints are missing: rerun them, or say so in the report.
- **A per-instruction metric** (CPI, misses per kilo-instruction): Σ wᵢ·Mᵢ / Σ wᵢ.
- **IPC:** 1 / (Σ wᵢ·CPIᵢ / Σ wᵢ). Never the weighted mean of the IPCs.
- **A run that didn't end in `instret_limit`** has no valid result: rerun it, or leave it out and say so.

## Gotchas

- **A replay prints nothing.** Only `from-sysroot` shows the program's output.
- **`from-sysroot` runs only on Linux.** The other kinds run on any host.
- **Only replays repeat the recorded run exactly.** A `from-sysroot` run's instruction count can differ from the trace's, e.g. with where make's output goes. Count instructions with a replay (`from-fesvr-trace`, `from-checkpoint`, `from-simpoint`).
- **Checkpoint names that repeat across traces** need the `TRACE:NAME` form. A bare ambiguous name fails with status 4, and the message lists the full forms.
- **`from-simpoint` needs SimPoint information.** On another checkpoint it fails with status 4; use `from-checkpoint` for those.
- **Quote globs** in `fwm list` filters, or the shell expands them.
- **Read access is enough** for everything here. Never edit files in the repository, and never run `fwm repo` commands, even those shown in `--help` examples (e.g. `fwm repo compose set-checkpoint-validation`, which records a checkpoint's validation): they are the maintainer's.
- **Option letters differ between simulators** (e.g. `-s`): check `<simulator> -h` before passing one.
- **`from-simpoint` already passes `-e`.** Adding another `-e` changes what is simulated.
- **fwm's exit statuses:**
  - 0: success;
  - 1: unexpected error;
  - 2: usage error, or not a usable repository;
  - 3: not found;
  - 4: a failed check (e.g. an ambiguous name, or a run directory of another kind);
  - 5: already exists (e.g. `fwm spawn` into an existing directory).

## Not for users: creating a repository

Adding sysroots, entry points, traces and checkpoints is done with `fwm repo` (`fwm repo --help`), usually by the fwm-bootstrap pipeline, which records traces and creates SimPoint checkpoints for a whole suite. A user who needs a workload that isn't in the repository should ask the repository's maintainer.

---

Verified with fwm at commit 4f058ce, on spike and 721sim, against a test repository.
