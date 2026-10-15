# fwm: the FESVR Workload Manager

fwm manages RISC-V simulation workloads for the custom spike in `riscv-isa-sim`, and replaces riscv-simenv.

That spike's front-end server (FESVR) can **record** the device traffic of a run: every proxied system call, with the memory it read and wrote. It can then **replay** that recording without the original environment, and create and load **checkpoints** on top of a replay (see `riscv-isa-sim/docs/fesvr-checkpoint-and-traffic-replay.md`). fwm keeps the pieces of that workflow in a repository and generates the Makefiles that run them:

```
sysroot ──(entry point)──> record ──> trace ──> replay + create checkpoints ──> load a checkpoint
```

## How it differs from riscv-simenv

- **The sysroot is needed once.** Only the recording run executes system calls on the host. A replay, or a run from a checkpoint, needs only the trace (which keeps a copy of the boot ELF as `boot.elf`), the checkpoint and the same `-m`/`-p`. There is no simenv to spawn and verify before every run.
- **A replay prints nothing and writes no file.** Console output and output files exist only in the recording run. A replay checks that the program computes the same data, byte for byte, and stops at the first difference.
- **Restore cost doesn't grow with the syscall history.** Loading a checkpoint seeks in the recording instead of re-executing it.
- **Traces and checkpoints are portable.** They replay and load on any little-endian host (x86-64 or ARM64 Linux, macOS). Recording must be done on Linux.

## Concepts

| Term | Meaning |
| --- | --- |
| **sysroot** | A directory tree the program sees as `/`, imported read-only into the repository with a catalog (every file's permissions and SHA-256). |
| **workload** | A name grouping the entry points that run the same program. |
| **entry point** | How to launch a workload in a sysroot: the command, working directory, boot ELF (usually pk), stdin file, memory size and processor count. A workload can have entry points on several sysroots, and a sysroot can serve several workloads. |
| **trace** | A recording (`+dev-traffic-record`) of a run of an entry point, identified by a UUID. It is *complete* if the program exited, or *truncated* if the run was stopped first (e.g. by `-e`). |
| **checkpoint** | A snapshot taken while replaying a trace. It loads only with that trace. A SimPoint checkpoint carries its SimPoint index, interval and weight. |

## Install

```
uv sync                # in this repository: creates .venv with fwm
uv run fwm --help
# or install the command: uv tool install .
```

## Shell completion

fwm completes its commands and options on Tab, like riscv-simenv. It also completes the names in the repository: sysroots, workloads, entry points, traces and checkpoints. The names come from the repository given by `--repo-path` or `FWM_REPO_PATH`; without one, nothing is offered. Enable it in your shell's startup file:

```
eval "$(fwm completion zsh)"            # ~/.zshrc
eval "$(fwm completion bash)"           # ~/.bashrc
fwm completion fish | source            # ~/.config/fish/config.fish
```

- **`fwm` must be on `PATH`**, e.g. with `uv tool install .`, or with this repository's `.venv/bin` on `PATH`. `uv run fwm` alone is not enough: the shell calls `fwm` to complete.
- **Faster shell start-up:** `eval` runs fwm each time a shell starts. To avoid that, write the script once to a file and source it, e.g. for zsh `fwm completion zsh > ~/.zfunc/_fwm` with `~/.zfunc` in `fpath` before `compinit`. Write it again after updating fwm.
- **Checkpoints** are completed by name when the name is unique, and otherwise as `<trace UUID>:<name>`. Typing a trace prefix and `:` narrows the list to that trace's checkpoints.
  - bash splits words at `:`, so it can't complete that form. zsh and fish can.
  - zsh and fish also show a short description with each name.

## Quick start

```
export FWM_REPO_PATH=~/fwm-repo                      # or pass --repo-path to every command
export FWM_SIM_CMD=/path/to/spike                    # default: spike on PATH

fwm repo init $FWM_REPO_PATH
fwm repo add sysroot ./sysroots/gapbs-road road.sg
fwm repo add entry road.sg bfs bfs-road-n64 bfs-road-n64.json
```

`bfs-road-n64.json` holds the launch fields of the entry point. In paths, `/` is the sysroot's root and `.` the working directory:

```json
{ "command": "bfs -f road.sg -n64", "working_directory": "/app",
  "envs": null, "stdin_file": null, "boot_elf": "/boot/pk", "mem_mb": 2048, "nprocs": 1 }
```

- `envs` must be `null` or `{}`: FESVR passes no environment to the program.
- `nprocs` must be 1: checkpoints can be created for a single HART only.
- `boot_elf` and `stdin_file` must be regular files of the sysroot, not symlinks.

Record a run, and import the recording as a trace, with the `spike --version` output for provenance:

```
spike --version > sim-version.txt
fwm mkgen record bfs-road-n64 -o run1 && make -C run1 run
fwm repo add trace bfs-road-n64 run1/trace --final-state run1/final-state.json --sim-version sim-version.txt
# prints the trace UUID
```

Replay it, and create checkpoints from a description file of `<name>: <instruction count>` lines. Then import them in one transaction. `--desc` checks that every checkpoint of the description was created, and `--simpoint-info` adds their SimPoint index, interval and weight:

```
fwm mkgen replay bfs-road-n64 -o run2 && make -C run2 run                         # final-state.json: same instret
fwm mkgen replay bfs-road-n64 --create-checkpoints ckpts.txt -o run3 && make -C run3 run
fwm repo add checkpoint bfs-road-n64 run3/checkpoints/* --desc run3/ckpt.desc --simpoint-info simpoints.json \
    --sim-version sim-version.txt
```

Validate a checkpoint by running it to the end, and simulate one interval from it:

```
fwm mkgen load bfs-road-n64.3.0.25 -o run4
make -C run4 run; fwm repo record-validation bfs-road-n64.3.0.25 run4      # passed or failed, recorded
make -C run4 run SIM_FLAGS_EXTRA=-e100000000     # make reports "Error 1": spike exits 1 when -e stops it
make -C run4 run SIM=/path/to/721sim SIM_FLAGS_EXTRA=-e100000000
```

## Commands

| Command | Purpose |
| --- | --- |
| `fwm repo init <path>` | Create a repository. |
| `fwm repo add sysroot <dir> <name>` | Import a sysroot. |
| `fwm repo add entry <sysroot> <workload> <name> <entry_point.json>` | Add an entry point. |
| `fwm repo add trace <entry> <recording-dir> [--final-state f.json] [--sim-version f]` | Import a recording; prints its UUID. |
| `fwm repo add checkpoint <trace> <file>... [--desc d] [--simpoint-info j] [--sim-version f]` | Import checkpoints in one transaction; prints `<uuid>:<name>` for each. |
| `fwm repo record-validation <checkpoint> <run-dir>` | Judge a `mkgen load` run to the end, and record passed or failed. |
| `fwm repo remove {sysroot\|entry\|trace\|checkpoint} <id> [--recursive]` | Remove, with what depends on it when `--recursive`. |
| `fwm repo verify [{sysroot\|trace\|checkpoint} <id>]` | Check the repository content against its catalogs, and for leftovers. |
| `fwm repo gc [--dry-run]` | Remove the leftovers of interrupted commands. |
| `fwm list [workloads\|sysroots\|entries\|traces\|checkpoints] [filter] [--json]` | List. |
| `fwm show {workload\|sysroot\|entry\|trace\|checkpoint} <id> [--json]` | Show the details. |
| `fwm spawn [--force] <sysroot> <dest>` | Make a working copy of a sysroot (the record Makefile does it). |
| `fwm mkgen {record <entry> \| replay <trace> [--create-checkpoints f] \| load <ckpt>} [-o dir]` | Generate a run directory's Makefile. |
| `fwm completion {bash\|zsh\|fish}` | Print the shell completion script. |

How to name things:
- **A trace** is its UUID (a unique prefix is enough), or the name of an entry point that has one trace.
- **A checkpoint** is `<trace>:<name>`, or just its name when no other trace has a checkpoint of that name.
- **A checkpoint's SimPoint weight** comes from `--simpoint-info` or `--simpoint-weight`. Without either, it is decoded from a name following the riscv-simenv convention `<entry>.<simpoint index>.<weight>`, e.g. `bfs-road-n64.3.0.25`.

Every `repo add` takes `--exist-ok`: if the same item is already in the repository, it prints it and succeeds. Without it, adding what exists fails with status 5. A recording that already is a trace of the entry point counts as existing, so a rerun never creates a second trace.

Exit status: 0 success, 1 unexpected error, 2 usage error or unusable repository, 3 not found, 4 failed check, 5 already exists.

## Generated Makefiles

Every Makefile has a single target, `run`. It first removes that run's previous outputs, so it can be run again. Override variables on the make command line:

| Variable | Use |
| --- | --- |
| `SIM` | The simulator (default: `FWM_SIM_CMD` when generated, else `spike`). |
| `SIM_FLAGS_EXTRA` | spike options such as `-e<n>` or `-s<interval>`. They come before the FESVR options. |
| `FESVR_FLAGS_EXTRA` | FESVR options such as `+verbose`. |
| `BOOT_ELF_FLAGS_EXTRA`, `APP_CMD_EXTRA` | (record only) Options of the boot ELF (pk), and extra program arguments. |
| `FWM` | (record only) How to run fwm for `spawn` (default: the Python that generated the Makefile, so fwm needn't be on `PATH`). |

spike's exit status:
- **Without `-e`:** the program's exit status, or 0 after creating the last checkpoint.
- **With `-e<n>`:** 1 when the run was stopped after `n` instructions, and 0 when the program ended first.

**Don't judge a run by make's exit status; let fwm judge it.** A program that legitimately exits non-zero makes `make` fail, while spike stopped by SIGTERM (e.g. a job's time limit) exits 0. `fwm repo add trace --final-state` judges a record run, `fwm repo add checkpoint --desc` a checkpoint creation, and `fwm repo record-validation` a run from a checkpoint.

`final-state.json` is written when the simulation ends: normally (the program exited, or the last checkpoint was taken), or when spike is stopped by SIGTERM or SIGINT, with the instruction count where it stopped. It stays empty after an `-e` stop or a simulator error, e.g. a replay that diverged from its recording.

The Makefiles leave other simulator outputs, such as `bbv_proc_0.bb.gz` from `-s`, to the caller: use a fresh run directory, or remove them.

## Clusters and shared repositories

- **Commands that change the repository** (`repo init`, `add`, `remove`, `gc`, `record-validation`) should run on one host, e.g. a Snakemake controller. The database uses SQLite's rollback journal, which works on a shared filesystem, and they wait for each other.
- **Compute jobs** run the generated Makefiles. Only a record run opens the database, read-only, when `fwm spawn` checks the sysroot out.
- **Read-only use:** `list`, `show`, `verify`, `mkgen`, `spawn` and completion work in a repository you can't write to, e.g. one shared with your group.
- **Interrupted commands:** a command killed by its scheduler (SIGTERM) cleans up after itself. A harder kill can leave staging or files that no row refers to. `fwm repo verify` reports them and `fwm repo gc` removes them; it never leaves rows whose files are missing.

## Development

```
uv run pytest            # unit tests
uv run mypy fwm tests
uv run ruff check fwm tests
```

The end-to-end test needs a spike built from `riscv-isa-sim` and a small workload:

```
FWM_TEST_SPIKE=/path/to/spike FWM_TEST_SYSROOT=/path/to/sysroot FWM_TEST_ENTRY_POINT=/path/to/entry_point.json \
FWM_TEST_SPIKE_SRC=/path/to/riscv-isa-sim uv run pytest tests/integration
```

The bootstrap pipeline's interface to fwm is described in [docs/fwm-bootstrap-interface.md](docs/fwm-bootstrap-interface.md).
