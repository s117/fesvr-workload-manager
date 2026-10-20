# fwm: a guide for coding agents

Read this before changing fwm. It gathers what earlier sessions learned: what fwm is for, how it is built, the spike behaviour it depends on, the decisions behind it, and how the user likes work done. For using fwm, see [README.md](../README.md). For the contract with the pipeline that fills repositories, see [fwm-bootstrap-interface.md](fwm-bootstrap-interface.md). Keep this file current when something here changes.

## 1. What fwm is

fwm (FESVR Workload Manager, package `fesvr-workload-manager`, command `fwm`) manages RISC-V simulation workloads for a custom spike. It replaces riscv-simenv (`~/Garage/riscv-simenv`; the agent skill `use-riscv-simenv` describes it).

The custom spike lives in `~/Garage/riscv-isa-sim`, with its FESVR as the submodule `riscv-fesvr`. Its guide is `riscv-isa-sim/docs/fesvr-checkpoint-and-traffic-replay.md`. Its FESVR can:
- **record** a run's device traffic (every proxied system call, with the memory it read and wrote) into a folder;
- **replay** that recording without the original environment: no system call runs again, so a replay prints nothing and writes no file;
- **create and load checkpoints** while replaying.

fwm keeps the pieces in a repository (SQLite plus files), and generates the Makefiles that run spike:

```
sysroot --(entry point)--> record --> trace --> create checkpoints --> checkpoints
```

**Two audiences.** Keep every new feature on the right side of this split:
- **The repository's creator** imports sysroots and entry points, records, and imports traces and checkpoints: `fwm repo ...`, including `fwm repo bootstrap record` / `create-checkpoints`. In practice this is the pipeline **fwm-bootstrap** (`/usr/local/usrapps/rotenberg/fwm-bootstrap`, its own guide in `docs/agent-guide.md`).
- **Its users** often have read-only access: `fwm list`, `show`, `verify`, `spawn` and `mkgen from-sysroot | from-fesvr-trace | from-checkpoint | from-simpoint`. Every one of these must work on a repository they can't write to.

**Repositories:**

| Remote | Status |
| --- | --- |
| `ncsu` = `git@github.com:ncstate-ece/Rotenberg-Student-jli95-fesvr-workload-manager.git` | the one fwm-bootstrap depends on |
| `origin` = `git@github.com:s117/fesvr-workload-manager.git` | the older remote |

The branch is `main`. Never push without being asked.

## 2. Code map

| Path | What |
| --- | --- |
| `fwm/cli/fwm.py` | The click group: subcommands imported when they run (`_SUBCOMMANDS`), `--repo-path` / `FWM_REPO_PATH`, `open_repo(ctx, write=...)`, `FwmError` → exit code, SIGTERM/SIGHUP → `SystemExit(128+sig)` so cleanup runs |
| `fwm/cli/subcmds/` | `repo` (`add`, `remove`, `verify`, `gc`, `set-metadata`, `record-validation`, `pull`, `push`, `subrepo`, and `bootstrap` from `bootstrap.py`), `list`, `show`, `spawn`, `mkgen`, `check`, `completion` |
| `fwm/cli/completion.py`, `output.py` | Shell completion (opens the repository read-only); table, JSON and summary output |
| `fwm/data_loader/` | Readers and pydantic models of spike's files: `recording.py`, `provenance.py`, `final_state.py`, `checkpoint.py` (+ digest), `ckpt_desc.py`, `simpoint_info.py`, `sim_version.py` (`spike --version`), `entry_point.py`, `file_catalog.py` |
| `fwm/database/` | peewee models (`schema.py`), `db.py` (`open_db`, `SCHEMA_VERSION`, journal, busy timeout), `catalog_store.py`, `custom_field.py` (`TimestampTzField`) |
| `fwm/repo/` | The operations: `repo.py` (`Repo`, staging, `importing()`, `removal()`), `sysroot.py`, `entry.py`, `trace.py`, `checkpoint.py`, `validation.py`, `spawn.py`, `remove.py`, `gc.py`, `transfer.py` (copying entry points between repositories: `pull`, `push`), `subrepo.py`, `query.py` (the JSON views), `lookup.py` (finding a sysroot, entry point, trace or checkpoint by name), `errors.py` |
| `fwm/mkgen/` | `makefile.py` (generators, quoting, `read_run_info`), `run_json.py`, `templates/*.mk` (`header.mk` + one per run kind) |
| `fwm/utils/` | file catalogs and their verification, digests, `fs.py` (copy with reflinks, read-only trees, `remove_tree`), target-path normalisation like FESVR's |
| `tests/` | `fesvr_files.py` writes fake recordings, checkpoints, provenance and final states; `conftest.py` has the fixtures; `integration/test_spike_e2e.py` runs a real spike |

**Repository on disk:**
- `fwm.db`;
- `sysroots/<name>/`;
- `traces/<uuid>/` (the recording, including `boot.elf` and `recording_provenance.json`);
- `checkpoints/<trace uuid>/<name>`;
- `.staging/`.

Stored content is made read-only, so delete a repository with `chmod -R u+w <dir> && rm -rf <dir>`.

## 3. Invariants to preserve

- **The CLI is an interface** (see the interface doc):
  - stdout carries results, stderr messages;
  - exit codes are 0 ok, 1 unexpected, 2 usage or unusable repository, 3 not found, 4 failed check, 5 already exists;
  - every `repo add` takes `--exist-ok`, and identical re-imports succeed with the same identifier;
  - `list --json` and `show --json` print the same views (`fwm/repo/query.py`).
- **Crash safety:**
  - imports copy into `.staging/` (each with a flock'd `.lock`), then rename into place inside the DB transaction (`Repo.importing()`), and move back if the commit fails;
  - removals commit first, then delete files (`Repo.removal()`);
  - a crash can leave only untracked files, never rows without files;
  - `repo verify` reports leftovers, and `repo gc` removes them.
- **Shared filesystems:**
  - SQLite uses the rollback journal, not WAL, with a 30 s busy timeout and `BEGIN IMMEDIATE`;
  - read-only users open with `?mode=ro`;
  - commands that write should run on one host.
- **Fast read-only commands:** on GPFS every imported module costs file lookups, a few ms each and sometimes 100 ms on a busy login node, so importing everything made `fwm list` take seconds:
  - `list`, `show`, `mkgen` and shell completion must not import pydantic. `list` and `show` must not import colorama or `importlib.metadata` either. `tests/cli/test_imports.py` checks this;
  - keep heavy imports out of the modules they load: `fwm/cli/` (except the writer subcommands), `lookup.py`, `query.py`, `repo.py`, `fwm/database/` (except `catalog_store.py`, which loads pydantic: read a catalog only in the commands that need one), `fwm/utils/fs.py`, `log.py`, `version.py`. Where one of these needs a heavy module, import it inside the function, with a comment;
  - a new subcommand goes in `_SUBCOMMANDS` in `fwm/cli/fwm.py`, with its one-line short help, not `cli.add_command()`. `fwm --help` and completing a subcommand name read the help from there, so they import no subcommand.
- **Schema changes:** bump `SCHEMA_VERSION` in `fwm/database/db.py` and don't write migrations. The user rebuilds repositories, which so far are all test repositories. Give them the list of repository paths to delete.
- **Generated Makefiles:**
  - every value is shell-quoted then make-escaped (`shell_value`), and newlines are refused;
  - each starts with `# fwm-run: {json}` (`read_run_info`), and writes `run.json`;
  - a run directory may only be reused for the same kind of run;
  - Makefiles create no extra files and clean only their own outputs. Callers handle the rest, such as BBV files.
- **Judge runs by spike's outputs, never by make's exit status:** a program can exit non-zero legitimately, and spike stopped by SIGTERM exits 0.

## 4. Spike behaviour fwm relies on (check the spike source, not only its guide)

Verify formats and options in `riscv-isa-sim` / `riscv-fesvr` source before designing a feature; the user expects this.

**Minimum versions.** Toolchains with spike are installed at `/usr/local/usrapps/rotenberg/anycore-riscv/install-linux-<commit>/`; use the newest one (on 2026-10-20, `install-linux-df3c68d`: riscv-isa-sim c303463 / riscv-fesvr f8a1a42). The older `.../jli95/anycore-riscv/install-linux` is gone.

| riscv-isa-sim / riscv-fesvr | What fwm needs from it |
| --- | --- |
| 2d50474 | the count at the program's exit no longer depends on where the run started (the dump before it was off by up to a batch, 135 instructions on every quick-test app) |
| eb87256 / 6f2d3b8 | the final state dump's `finish_reason` |
| 872d191 / ecc3df3 | a repeated SIGTERM is ignored, and the dump is written atomically |
| fd01c25 / 8472993 | `recording_provenance.json` in every recording (required) |

**Recordings** (`fwm/data_loader/recording.py`):
- the folder holds `target_spec.data`, `boot.elf`, `recording_provenance.json`, and per hart `device_traffic_hart<N>.packets` / `.index` / `.sha256` (gzip by default);
- replay rejects a target program;
- `+chroot` is a plain path prefix: the host resolves symlinks, and `readlink` returns host paths;
- exit status: `sys_exit` sets exitcode = `code<<1|1`, and fwm stores `(code>>1)&0xff`;
- a recording without an exit command is truncated.

**Provenance** (`fwm/data_loader/provenance.py`):
- contents: `started`, `finished` (absent if the process died), `time_zone`, `uname`, `command` (`argv`, `cwd`), `simulator` (`name`, `build`), `fesvr`;
- times look like `2026-10-17T18:57:50-0400`;
- build fields: `build_hostname`, `build_host`, `build_target`, `cxx_compiler`, `build_date`, `build_type`, `repository`, `version`, `commit`;
- re-recording a replay copies the replayed recording's file;
- fwm stores the whole JSON, plus `recorded_on` / `recording_started` / `recording_finished` columns (schema v8). Unknown keys are accepted.

**Final state dump** (`+final-state-dump`, `fwm/data_loader/final_state.py`):
- `finish_reason` is one of:
  - `target_exit` (with `exit_code`);
  - `checkpoints_created`;
  - `instret_limit` (`-e`; spike exits 1);
  - `debug_quit`;
  - `interrupted` (with `signal`; spike exits 0);
  - `error` (with `error`, no `hart_state`);
  - `unfinished` (no `hart_state`);
- written as `unfinished` at start, replaced atomically at the end;
- `instret` counts since the original boot, including the part before a restored checkpoint;
- a dump without `finish_reason` (older spike) is refused.

**Checkpoints:**
- format v1: a 48-byte header, the portable `RVHSTATE` hart state (80 entries, including `count` and the load reservation), then a zlib memory image;
- `content_sha256` = SHA-256 of `riscv-isa-sim/scripts/checkpoint-digest.py`'s output (`checkpoint_digest_lines()`), so it doesn't depend on compression;
- created for one hart only; checkpoints at or after the program's exit are silently not created (hence `repo add checkpoint`'s check against the description);
- spike creates checkpoints at the counts of a description file, `<name>: <count>`, and the run ends after the last one;
- a count of 0 is allowed, and `+create-checkpoint` can combine with `+load-checkpoint`, counting from the restore. That is a good way to compare states.

**Validation** (`record-validation`): a run from a checkpoint passes only on `target_exit` with the trace's per-hart `instret` and exit status. A replay stops at the first divergence, so reaching the end proves the output.

**BBV / SimPoint:** `-s<interval>` writes `bbv_proc_<hart>.bb.gz`. Intervals aren't exactly `<interval>` long, and the last partial one is dropped, so a SimPoint at interval `i` starts at the sum of the sizes of intervals `0..i-1`.

**Signals:**
- spike's SIGINT opens its debug mode;
- SIGTERM is handled by the FESVR;
- under make or `kill -- -<pgid>`, spike gets SIGTERM twice, which is fine from ecc3df3 on.

**Spike's `-d` debug mode** prints every instruction and counts differently from normal runs. Don't use it to count.

**Load reservation:** dropped on a trap, on a host memory write, and at counts that are multiples of 8192, so SC results don't depend on where a run started.

## 5. Decisions and why

**User decisions (2026-10):**
- **Spawning:** `copy_spawn` is an entry-point launch field; `fwm spawn [--mode auto|copy|link] ENTRY DEST`; `auto` links when `copy_spawn` is false. A linked sysroot has real directories, and each file is an absolute symlink to the repository's read-only file.
- **Metadata:** an entry point has free-form `metadata`, replaced with `repo set-metadata`; `--exist-ok` ignores differences in it.
- **Checkpoint import:** `repo add checkpoint <trace> <desc> <checkpoint-dir> [--simpoint-info] [--sim-version]`. The SimPoint info is `{"interval", "maxK", "checkpoints": {name: {"index", "weight"}}}`; weights are never decoded from names.
- **Run generators:** `mkgen` is for users; `repo bootstrap` is for the creator and only reads the repository.
- **Old spikes:** old dumps and recordings without provenance are refused rather than supported. The repositories get rebuilt instead.
- **`--sim-version`:** `repo add trace` has none, because the provenance holds the recording spike's build. Checkpoints keep it, since their files carry no provenance.
- **Copying between repositories** (2026-10-20): one mechanism, `fwm/repo/transfer.py`, serves merging and subsets.
  - `repo pull SOURCE` writes `--repo-path`; `repo push DEST` reads it; `repo subrepo NEW` is `init` + `push`, and removes `NEW` on failure.
  - It picks entry points (or `--all`), each bringing its sysroot, traces and checkpoints (`--no-traces`, `--no-checkpoints`).
  - Rows are copied as they are, trace UUIDs included.
  - What the destination has is kept under the `--exist-ok` rules. A trace is matched by its recording, so a destination trace with another UUID is kept and the checkpoints attach to it.
  - Destination rows never change; there are no updates or hard links.
  - Conflicts are all checked first, and then nothing is copied.
  - Each entry point is staged, verified against its catalogs and committed on its own, so reruns complete an interrupted copy.
  - Two repositories are open at once: the destination is bound to `database_proxy` (`Repo.bound()`), and the source is read through `bind_ctx` after `Repo.open(..., bind=False)`.
  - `pull`'s completion reads its `SOURCE` argument, not `--repo-path`.

**Kept deliberately:**
- the Makefiles keep a minimal behaviour (no extra files, no BBV cleanup);
- there is no `spawn --catalog`;
- `check recording --json` has no `finish_reason` (a passing check always means `target_exit`).

## 6. Working with the user

- **Commits:** commit each logical step on its own, after its tests pass. Messages follow 50/72, with a lowercase imperative subject (e.g. "store each trace's provenance"), and end with the attribution trailer the session's instructions specify. Never push unless asked.
- **Plans:** non-trivial work goes through plan mode. The user reviews the plan and comments on it, sometimes several times. Plans and documents must be readable: plain words, short sentences, tables and lists, no jargon, no dense paragraphs. The user has rejected unreadable plans.
- **Ask, don't assume,** when a decision is the user's: behaviour changes, compatibility, what to delete. Offer a recommended option.
- **Spike bugs:** when fwm hits one, don't patch around it silently. Reproduce it with spike alone, and write a problem description with a reproduction for the riscv-isa-sim agent. Earlier ones are in `fwm-bootstrap/vcs_untracked/quicktest/mcf29-repro/PROBLEM.md` and `.../sigterm-repro/PROBLEM.md`. Then verify the fix the user installs.
- **The user edits files and history too.** If a file changed on disk, take it as intended. Commits may be rebased: check `git log` before assuming your hashes.

## 7. Development

```sh
uv run pytest                    # ~420 tests, ~15 s
uv run mypy fwm tests
uv run ruff check fwm tests      # pyproject pins ruff's former default rules (E4, E7, E9, F)
```

**Spike end-to-end test** (`tests/integration/test_spike_e2e.py`, ~45 s): it needs a helloworld sysroot built with the new toolchain. Scratch files vanish between sessions; rebuild it with:

```sh
R=/usr/local/usrapps/rotenberg/anycore-riscv/install-linux-df3c68d   # or the newest install-linux-*
S=<scratch dir>; mkdir -p $S/sysroot/app $S/sysroot/boot
$R/bin/riscv64-unknown-linux-gnu-gcc -static -Wl,-Ttext-segment,0x10000 -O2 -o $S/sysroot/app/helloworld.rv64 \
    /usr/local/usrapps/rotenberg/fwm-bootstrap/suites/helloworld/sysroots/000.helloworld/src/helloworld.c
cp $R/riscv64-unknown-elf/bin/pk $S/sysroot/boot/pk
echo '{"command": "helloworld.rv64", "working_directory": "/app", "envs": null, "stdin_file": null,
       "boot_elf": "/boot/pk", "mem_mb": 256, "nprocs": 1}' > $S/entry.json
FWM_TEST_SPIKE=$R/bin/spike FWM_TEST_SYSROOT=$S/sysroot FWM_TEST_ENTRY_POINT=$S/entry.json \
FWM_TEST_SPIKE_SRC=~/Garage/riscv-isa-sim uv run pytest tests/integration
```

**Building spike** (only if the installed one is older than needed):
- configure `~/Garage/riscv-isa-sim` with CMake (`-DCMAKE_BUILD_TYPE=RelO3 -DENABLE_DBG_TRACE=1`);
- build `--target spike` only, since an untracked extension fails to build;
- check `spike --version`'s commits against the checkout. The user usually installs new builds themselves.

**Pitfalls:**
- **Two environments:** a shell started in fwm-bootstrap may carry its `VIRTUAL_ENV`, so `uv` warns. Harmless.
- **No system Python:** `/usr/bin/python3.12` is on the login node's local disk but not on every compute node, so fwm runs on uv's Python, which lives on GPFS (`~/.local/share/uv`). Startup speed has to come from importing less.
- **zsh word splitting:** the user's shell is zsh, so `$var` holding several words isn't split. Use bash for such loops.
- **Signals in tests:** wait for the whole process group to exit before reading a dump. make ends before spike finishes writing.
- **`tests/fesvr_files.py`** writes fake files that must match the real formats. When spike changes a format, change the fakes and the readers together.
