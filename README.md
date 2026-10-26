# fwm: the FESVR Workload Manager

fwm manages RISC-V simulation workloads for the custom spike in `riscv-isa-sim`, and replaces riscv-simenv.

That spike's front-end server (FESVR) can **record** the device traffic of a run: every proxied system call, with the memory it read and wrote. It can then **replay** that recording without the original environment, and create and load **checkpoints** on top of a replay (see `riscv-isa-sim/docs/fesvr-checkpoint-and-traffic-replay.md`). fwm keeps the pieces of that workflow in a repository and generates the Makefiles that run them.

**The repository's creator** builds its content with `fwm repo`: `fwm repo bootstrap` generates the runs that record traces and create checkpoints, and `fwm repo compose` imports them:

```
sysroot ──(entry point)──> record-fesvr-trace ──> trace ──> create-checkpoints ──> checkpoints
```

**Its users,** who may have read access only, begin simulations with `fwm mkgen`: from a sysroot, from a FESVR trace, from a checkpoint, or for one SimPoint interval.

## How it differs from riscv-simenv

- **The sysroot is needed once.** Only the recording run (and `mkgen from-sysroot`, which runs the program for real) executes system calls on the host. A replay, or a run from a checkpoint, needs only the trace (which keeps a copy of the boot ELF as `boot.elf`), the checkpoint and the same `-m`/`-p`. There is no simenv to spawn and verify before every run.
- **A replay prints nothing and writes no file.** Console output and output files exist only in the recording run. A replay checks that the program computes the same data, byte for byte, and stops at the first difference.
- **Restore cost doesn't grow with the syscall history.** Loading a checkpoint seeks in the recording instead of re-executing it.
- **Traces and checkpoints are portable.** They replay and load on any little-endian host (x86-64 or ARM64 Linux, macOS). Recording must be done on Linux.

## Concepts

| Term | Meaning |
| --- | --- |
| **sysroot** | A directory tree the program sees as `/`, imported read-only into the repository with a catalog (every file's permissions and SHA-256). |
| **workload** | A name grouping the entry points that run the same program. |
| **entry point** | How to launch a workload in a sysroot: the command, working directory, boot ELF (usually pk), stdin file, memory size and processor count, with optional free-form metadata. A workload can have entry points on several sysroots, and a sysroot can serve several workloads. |
| **trace** | A recording (`+dev-traffic-record`) of a run of an entry point, identified by a UUID. It is *complete* if the program exited, or *truncated* if the run was stopped first (e.g. by `-e`). |
| **checkpoint** | A snapshot taken while replaying a trace. It loads only with that trace. A SimPoint checkpoint carries its SimPoint index, interval, weight and the analysis's maxK. |

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
fwm repo compose add sysroot ./sysroots/gapbs-road road.sg
fwm repo compose add entry road.sg bfs bfs-road-n64 bfs-road-n64.json
```

`bfs-road-n64.json` holds the launch fields of the entry point. In paths, `/` is the sysroot's root and `.` the working directory:

```json
{ "command": "bfs -f road.sg -n64", "working_directory": "/app",
  "envs": null, "stdin_file": null, "boot_elf": "/boot/pk", "mem_mb": 2048, "nprocs": 1, "copy_spawn": false,
  "metadata": { "suite": "GAPBS", "description": "Breadth-First Search on 'road'", "language": ["C++"] } }
```

- `envs` must be `null` or `{}`: FESVR passes no environment to the program.
- `nprocs` must be 1: checkpoints can be created for a single HART only.
- `boot_elf` and `stdin_file` must be regular files of the sysroot, not symlinks.
- `copy_spawn` (optional, default `true`) is how a run of the program gets its sysroot (see `fwm spawn`): a copy, or, when `false`, links to the repository's read-only files. Linking takes no time or space, even for gigabytes of input, but the program can't open its files for writing, `stat` reports them read-only, and `lstat` and `readlink` see links into the repository. Use `false` only for a program that just reads the files it finds.
- `metadata` is optional and free-form: any JSON object, such as a riscv-simenv suite's `index.yaml` metadata. fwm shows it and copies it into each run's `run.json`. Change it later with `fwm repo compose set-metadata`.

Record a run, and import the recording as a trace. The recording says how it was made (`recording_provenance.json`: when, on what machine, the command line, and the spike's and FESVR's builds), which the trace keeps:

```
fwm repo bootstrap mkgen record-fesvr-trace bfs-road-n64 -o run1 && make -C run1 run SIM_FLAGS_EXTRA=-s100000000   # with a BBV
fwm repo compose add trace bfs-road-n64 run1/trace --final-state run1/final-state.json
# prints the trace UUID
```

Checkpoint files don't say how they were made, so their import takes the `spike --version` output for provenance:

```
spike --version > sim-version.txt
```

Create checkpoints from a description file of `<name>: <instruction count>` lines (e.g. the SimPoints of the BBV), and import them in one transaction:

```
fwm repo bootstrap mkgen create-checkpoints bfs-road-n64 ckpts.txt -o run3 && make -C run3 run
fwm repo compose add checkpoint bfs-road-n64 run3/ckpt.desc run3/checkpoints --simpoint-info simpoints.json \
    --sim-version sim-version.txt
```

- **Every checkpoint of the description must be in `run3/checkpoints`,** which catches one spike didn't create: it creates none at or after the program's exit, without a word. Other files there are ignored.
- **`--simpoint-info`** gives the SimPoint analysis, naming exactly the checkpoints of the description:
  ```json
  {"interval": 100000000, "maxK": 10,
   "checkpoints": {"bfs-road-n64.3.0.25": {"index": 3, "weight": 0.2531}, "bfs-road-n64.7.0.75": {"index": 7, "weight": 0.7469}}}
  ```

Validate a checkpoint by running it to the end:

```
fwm mkgen from-checkpoint bfs-road-n64.3.0.25 -o run4
make -C run4 run; fwm repo compose set-checkpoint-validation bfs-road-n64.3.0.25 run4      # passed or failed, recorded
```

## Running workloads

`fwm mkgen` generates a run directory that begins a simulation; run it with `make -C <dir> run`:

```
fwm mkgen from-sysroot bfs-road-n64 -o a           # the real program in a fresh copy of its sysroot (Linux only)
fwm mkgen from-fesvr-trace bfs-road-n64 -o b       # replay its trace from the start: any host, no output
fwm mkgen from-checkpoint bfs-road-n64.3.0.25 -o c # from a checkpoint to the end of the program
fwm mkgen from-simpoint bfs-road-n64.3.0.25 -o d   # one SimPoint interval from a SimPoint checkpoint
make -C d run SIM=/path/to/721sim                  # make reports "Error 1": spike exits 1 when -e stops it
```

- **`from-sysroot`** does the program's real system calls, so it prints its console (also saved to `console.stdout` and `console.stderr`) and writes its files in `sysroot/`.
- **`from-fesvr-trace`, `from-checkpoint` and `from-simpoint`** replay the trace: the program prints nothing and writes no file.
- **`from-simpoint`** stops after the checkpoint's SimPoint interval (`-e$(INTERVAL)`, overridable with `INTERVAL=<n>`); weigh its results by the checkpoint's `simpoint_weight`.

## Commands

**Daily**, for every user of a repository (they work without write permission):

| Command | Purpose |
| --- | --- |
| `fwm list [workloads\|sysroots\|entries\|traces\|checkpoints] [filter] [--json]` | List. |
| `fwm show {workload\|sysroot\|entry\|trace\|checkpoint} <id> [--json]` | Show the details. |
| `fwm mkgen {from-sysroot <entry> \| from-fesvr-trace <trace> \| from-checkpoint <ckpt> \| from-simpoint <ckpt>} [-o dir]` | Generate a run directory that begins a simulation. |
| `fwm spawn [--force] [--mode auto\|copy\|link] <entry> <dest>` | Make a working copy of an entry point's sysroot: copied, or linked to the repository's files; `auto`, the default, follows its `copy_spawn` (the `from-sysroot` and record Makefiles do it). |
| `fwm completion {bash\|zsh\|fish}` | Print the shell completion script (setup; it isn't offered on TAB). |

**`fwm repo`**, for the repository's creator. The repository as a whole:

| Command | Purpose |
| --- | --- |
| `fwm repo init <path>` | Create a repository. |
| `fwm repo verify [{sysroot\|trace\|checkpoint} <id>]` | Check the repository content against its catalogs, and for leftovers. |
| `fwm repo gc [--dry-run]` | Remove the leftovers of interrupted commands. |
| `fwm repo pull <source> {<entry>... \| --all} [--no-traces] [--no-checkpoints]` | Copy entry points of another repository into this one, with their sysroots, traces and checkpoints. |
| `fwm repo push <dest> {<entry>... \| --all} [--no-traces] [--no-checkpoints]` | Copy entry points of this repository into another one. |
| `fwm repo subrepo <new-repo> <entry>... [--no-traces] [--no-checkpoints]` | Create a repository with some entry points of this one: `repo init`, then `repo push`. |

**`fwm repo compose`** changes the repository's content; each command needs write access:

| Command | Purpose |
| --- | --- |
| `fwm repo compose add sysroot <dir> <name>` | Import a sysroot. |
| `fwm repo compose add entry <sysroot> <workload> <name> <entry_point.json>` | Add an entry point. |
| `fwm repo compose add trace <entry> <recording-dir> [--final-state f.json]` | Import a recording, with its provenance; prints its UUID. |
| `fwm repo compose add checkpoint <trace> <desc> <checkpoint-dir> [--simpoint-info j] [--sim-version f]` | Import the checkpoints of a description in one transaction; prints `<uuid>:<name>` for each. |
| `fwm repo compose remove {sysroot\|entry\|trace\|checkpoint} <id> [--recursive]` | Remove, with what depends on it when `--recursive`. |
| `fwm repo compose set-metadata <entry> <file.json>` | Replace an entry point's metadata (`null` removes it), keeping its traces. |
| `fwm repo compose set-checkpoint-validation <checkpoint> <run-dir>` | Judge a `mkgen from-checkpoint` run to the end, and set the checkpoint's validation: passed or failed. |

**`fwm repo bootstrap`** makes the repository's traces and checkpoints, in the pipeline's order; its commands only read the repository:

| Command | Purpose |
| --- | --- |
| `fwm repo bootstrap mkgen record-fesvr-trace <entry> [-o dir]` | Generate a run directory that records a run of an entry point: its FESVR trace. |
| `fwm repo bootstrap check-fesvr-trace <recording-dir> [--final-state f.json] [--json]` | Check a recording as `repo compose add trace` would, without a repository: complete, well formed, agreeing with its final state. |
| `fwm repo bootstrap mkgen create-checkpoints <trace> <desc> [-o dir]` | Generate a run directory that creates the checkpoints of a description. |

How to name things:
- **A trace** is its UUID (a unique prefix is enough), or the name of an entry point that has one trace.
- **A checkpoint** is `<trace>:<name>`, or just its name when no other trace has a checkpoint of that name.
- **A checkpoint's name** is just a name: its SimPoint data comes only from `--simpoint-info`.

Every `repo compose add` takes `--exist-ok`: if the same item is already in the repository, it prints it and succeeds. An entry point that differs only in its description or metadata is the same; it keeps its own. Without it, adding what exists fails with status 5. A recording that already is a trace of the entry point counts as existing, so a rerun never creates a second trace.

Exit status: 0 success, 1 unexpected error, 2 usage error or unusable repository, 3 not found, 4 failed check, 5 already exists.

## Merging and subsets

`repo pull`, `repo push` and `repo subrepo` copy entry points from one repository to another. Each entry point brings its workload and sysroot, and its traces with their checkpoints, unless `--no-traces` or `--no-checkpoints` leave them out:

```
fwm repo subrepo ~/small bfs-road-n64 pr-road           # a new repository with two entry points
fwm --repo-path ~/small repo pull /shared/repo cc-road  # add a third one to it
fwm --repo-path /shared/repo repo push ~/merged --all   # merge everything into another repository
```

- **Rows are copied as they are:** names, trace UUIDs, provenance, metadata, SimPoint data and validation results.
- **What the receiving repository has is kept,** under the rules of `--exist-ok`:
  - a sysroot of the same name and content;
  - an entry point of the same name, sysroot, workload and launch fields, with its own metadata;
  - a trace made of the same recording, with its own UUID, to which the copied checkpoints attach;
  - a checkpoint of the same name and content, with its own validation result.

  Copying again adds nothing, and its rows never change.
- **Conflicts:** the same name with something else is a conflict. Every entry point is checked before anything is copied, and then nothing is: the command fails with status 5. If another command changes the receiving repository during the copy, a conflict found then stops it, keeping the entry points already committed.
- **Interruptions:** each entry point is committed on its own, like an import. An interrupted copy can be run again to complete it. `subrepo` removes its new repository if the copy fails (an empty directory it was created in is left empty, as it was).
- **Read access is enough** to copy from a repository. Completion offers the entry points of the repository copied from: for `pull`, those of its `<source>`.
- **Output:** each item prints as `added|present <kind> <id>` (`subrepo` prints the new repository's path), and a summary goes to stderr.

## Generated Makefiles

Every Makefile has a single target, `run`. It first removes that run's previous outputs, so it can be run again. A run directory can be generated again for the same kind of run, but not for another kind, whose Makefile wouldn't remove the former outputs.

Next to it, `run.json` describes what the run uses: the sysroot, workload, entry point (with its metadata), trace and checkpoint, as `fwm show --json` shows them when the Makefile is generated. Its `kind` says which command generated it, e.g. `from-simpoint`, or `record` for `repo bootstrap mkgen record-fesvr-trace`. A create-checkpoints run also lists the checkpoints it requests. See [docs/fwm-bootstrap-interface.md](docs/fwm-bootstrap-interface.md#runjson).

Override variables on the make command line:

| Variable | Use |
| --- | --- |
| `SIM` | The simulator (default: `FWM_SIM_CMD` when generated, else `spike`). |
| `SIM_FLAGS_EXTRA` | spike options such as `-e<n>` or `-s<interval>`. They come before the FESVR options. |
| `FESVR_FLAGS_EXTRA` | FESVR options such as `+verbose`. |
| `BOOT_ELF_FLAGS_EXTRA`, `APP_CMD_EXTRA` | (from-sysroot, record) Options of the boot ELF (pk), and extra program arguments. |
| `FWM` | (from-sysroot, record) How to run fwm for `spawn` (default: the Python that generated the Makefile, so fwm needn't be on `PATH`). |
| `INTERVAL` | (from-simpoint) The instructions to simulate (default: the checkpoint's SimPoint interval). |

spike's exit status:
- **Without `-e`:** the program's exit status, or 0 after creating the last checkpoint.
- **With `-e<n>`:** 1 when the run was stopped after `n` instructions, and 0 when the program ended first.

**Don't judge a run by make's exit status; let fwm judge it.** A program that legitimately exits non-zero makes `make` fail, while spike stopped by SIGTERM (e.g. a job's time limit) exits 0. `fwm repo compose add trace --final-state` judges a record run, `fwm repo compose add checkpoint` a checkpoint creation, and `fwm repo compose set-checkpoint-validation` a run from a checkpoint.

`final-state.json` says why the run finished, and each hart's instruction count there: `target_exit` (with `exit_code`), `checkpoints_created`, `instret_limit` (`-e`), `debug_quit`, `interrupted` (with `signal`), `error` (with the simulator's message, and no count) or `unfinished` (spike died). fwm needs a spike that writes `finish_reason` (riscv-isa-sim eb87256 / riscv-fesvr 6f2d3b8 or later): older ones wrote counts at the program's exit that depended on where the run started, and their dumps are refused. fwm also needs the recording's provenance (`recording_provenance.json`), written from riscv-isa-sim fd01c25 / riscv-fesvr 8472993 on, and refuses a recording without it. Those versions also include the fixes of riscv-isa-sim 872d191 / riscv-fesvr ecc3df3, needed in batch jobs: before them, a run that got SIGTERM twice (from a job's time limit, under make) could leave an empty or broken dump.

The Makefiles leave other simulator outputs, such as `bbv_proc_0.bb.gz` from `-s`, to the caller: use a fresh run directory, or remove them.

## Clusters and shared repositories

- **Commands that change the repository** (`repo init`, `gc`, `pull`, every `repo compose` command, and `push` / `subrepo` for the repository they write) should run on one host, e.g. a Snakemake controller. The database uses SQLite's rollback journal, which works on a shared filesystem, and they wait for each other.
- **Compute jobs** run the generated Makefiles. Only the `from-sysroot` and record runs open the database, read-only, when `fwm spawn` checks the sysroot out.
- **Read-only use:** `list`, `show`, `mkgen`, `spawn`, `repo verify`, every `repo bootstrap` command, `repo push` / `subrepo` (copying from it) and completion work in a repository you can't write to, e.g. one shared with your group.
- **Interrupted commands:** a command killed by its scheduler (SIGTERM) cleans up after itself. A harder kill can leave staging or files that no row refers to. `fwm repo verify` reports them and `fwm repo gc` removes them; it never leaves rows whose files are missing. A command committing at the moment `verify` runs can show as leftovers too: run it again.

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

Notes for coding agents (how fwm is built, the spike behaviour it relies on, decisions, conventions) are in [docs/agent-guide.md](docs/agent-guide.md).
