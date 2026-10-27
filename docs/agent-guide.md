# fwm: a guide for coding agents

Read this before changing fwm. It gathers what earlier sessions learned: what fwm is for, how it is built, the spike behaviour it depends on, the decisions behind it, and how the user likes work done. For using fwm, see [README.md](../README.md). For the contract with the pipeline that fills repositories, see [fwm-bootstrap-interface.md](fwm-bootstrap-interface.md). Keep this file current when something here changes.

**This file is public** (the repository is, and PyPI links to it). Keep it free of anything specific to one machine or site: no paths of a home directory, cluster or install, no host names, no private URLs. Those go in `docs/agent-guide.local.md`, which git ignores: read it too when it exists. Below, `<riscv-isa-sim>`, `<fwm-bootstrap>` and `<toolchain>` stand for the checkout or install it gives.

## 1. What fwm is

fwm (FESVR Workload Manager, package `fwm` on PyPI, command `fwm`) manages RISC-V simulation workloads for a custom spike. It replaces riscv-simenv (the agent skill `use-riscv-simenv` describes it).

The custom spike is [riscv-isa-sim](https://github.com/s117/riscv-isa-sim) (checked out at `<riscv-isa-sim>`), with its FESVR as the submodule `riscv-fesvr`. Its guide is `riscv-isa-sim/docs/fesvr-checkpoint-and-traffic-replay.md`. Its FESVR can:
- **record** a run's device traffic (every proxied system call, with the memory it read and wrote) into a folder;
- **replay** that recording without the original environment: no system call runs again, so a replay prints nothing and writes no file;
- **create and load checkpoints** while replaying.

fwm keeps the pieces in a repository (SQLite plus files), and generates the Makefiles that run spike:

```
sysroot --(entry point)--> record --> trace --> create checkpoints --> checkpoints
```

**Two audiences.** Keep every new feature on the right side of this split, which the CLI's layout follows (user decision, 2026-10-26):
- **Its users** often have read-only access. Their commands are the top level: `fwm list`, `show`, `mkgen from-sysroot | from-fesvr-trace | from-checkpoint | from-simpoint` and `spawn` (plus `completion`, for setup, which isn't offered on TAB). Every one of these must work on a repository they can't write to.
- **The repository's creator** uses `fwm repo`. In practice this is the pipeline **fwm-bootstrap** (at `<fwm-bootstrap>`, its own guide in `docs/agent-guide.md`):
  - the repository as a whole: `init`, `verify`, `gc`, and copying between repositories: `pull`, `push`, `subrepo`;
  - `fwm repo compose` changes the content, and each of its commands needs write access: `add {sysroot|entry|trace|checkpoint}`, `remove`, `set-metadata`, `set-checkpoint-validation`;
  - `fwm repo bootstrap` makes traces and checkpoints, and none of its commands writes: `mkgen record-fesvr-trace`, `mkgen create-checkpoints` (run generators, like `fwm mkgen`), and `check-fesvr-trace` (no repository at all).
  - Tests check both rules (`test_compose_needs_write_access`, `test_bootstrap_only_reads`): a new command goes in the group that matches its access, and in those tests.
- **Names are an interface:** fwm-bootstrap calls these commands, so renaming one means updating it in the same step (it pins fwm). The run kinds stored in run directories (`record`, `create-checkpoints`, …) are not command names and don't change with them.

**Repositories:**

| Remote | Status |
| --- | --- |
| `origin` = `git@github.com:s117/fesvr-workload-manager.git` | public since 2026-10-27; the source PyPI's page links to |
| `ncsu` | a private mirror (its URL is in the local notes) |

The branch is `main`. Never push without being asked.

**PyPI:** fwm is published as the package `fwm` (from 0.1.0), under NCSU's license (`LICENSE`, as riscv-simenv's). The README is its PyPI page, so it stays generic: no site paths, no private URLs, and absolute links to files of the repository (relative ones break on PyPI). A version can't be uploaded twice: bump `version` in `pyproject.toml` for each release. Build the release into its own folder (`uv build -o dist/fwm-<version>`) and upload that folder only.

## 2. Code map

| Path | What |
| --- | --- |
| `fwm/cli/fwm.py` | The click group: subcommands imported when they run (`_SUBCOMMANDS`), `--repo-path` / `FWM_REPO_PATH`, `open_repo(ctx, write=...)`, `FwmError` → exit code, SIGTERM/SIGHUP → `SystemExit(128+sig)` so cleanup runs |
| `fwm/cli/subcmds/` | `list`, `show`, `mkgen`, `spawn`, `completion`; `repo` (`init`, `verify`, `gc`, `pull`, `push`, `subrepo`), with `compose` from `compose.py` and `bootstrap` (its `mkgen` and `check-fesvr-trace`) from `bootstrap.py`. Groups list their commands in sections (`SectionedGroup` in `fwm/cli/fwm.py`). |
| `fwm/cli/completion.py`, `output.py` | Shell completion (opens the repository read-only); table, JSON and summary output |
| `fwm/data_loader/` | Readers and pydantic models of spike's files: `recording.py`, `provenance.py`, `final_state.py`, `checkpoint.py` (+ digest), `ckpt_desc.py`, `simpoint_info.py`, `sim_version.py` (`spike --version`), `entry_point.py`, `file_catalog.py` |
| `fwm/database/` | peewee models (`schema.py`), `db.py` (`open_db`, `SCHEMA_VERSION`, journal, busy timeout), `catalog_store.py`, `custom_field.py` (`TimestampTzField`) |
| `fwm/repo/` | The operations: `repo.py` (`Repo`, staging, `importing()`, `removal()`), `sysroot.py`, `entry.py`, `trace.py`, `checkpoint.py`, `validation.py`, `spawn.py`, `remove.py`, `gc.py`, `transfer.py` (copying entry points between repositories: `pull`, `push`), `subrepo.py`, `query.py` (the JSON views of `show`), `listing.py` (`list`: names, filtered by related entities), `lookup.py` (finding a sysroot, entry point, trace or checkpoint by name), `errors.py` |
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
  - exit codes are 0 ok, 1 unexpected, 2 usage or unusable repository, 3 not found, 4 failed check, 5 already exists (an item of the repository, or a destination: `spawn`, `repo init`, `subrepo`);
  - every `repo compose add` takes `--exist-ok`, and identical re-imports succeed with the same identifier;
  - `list` prints names, as `show` takes them (`fwm/repo/listing.py`); `show --json` prints the views (`fwm/repo/query.py`).
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
  - keep heavy imports out of the modules they load: `fwm/cli/` (completing a subcommand imports its module, so `repo.py` imports the readers, catalogs and copying inside its commands), `lookup.py`, `query.py`, `listing.py`, `repo.py`, `fwm/database/` (except `catalog_store.py`, which loads pydantic: read a catalog only in the commands that need one), `fwm/utils/fs.py`, `log.py`, `version.py`. Where one of these needs a heavy module, import it inside the function, with a comment;
  - a new subcommand goes in `_SUBCOMMANDS` in `fwm/cli/fwm.py`, with its one-line short help, not `cli.add_command()`. `fwm --help` and completing a subcommand name read the help from there, so they import no subcommand.
- **Schema changes need the user's approval first** (since 2026-10-26). Two real repositories were built with fwm `162c105` (schema version 8), and a schema change would make them unusable: fwm refuses a database of another `SCHEMA_VERSION`. So:
  - don't change `fwm/database/schema.py` or `SCHEMA_VERSION` (`fwm/database/db.py`) without asking, and look for a way that keeps the schema first;
  - if a change is truly needed, it comes with a migration of existing repositories (from version 8 on), designed with the user.
- **Generated Makefiles:**
  - every value is shell-quoted then make-escaped (`shell_value`), and newlines are refused;
  - each starts with `# fwm-run: {json}` (`read_run_info`), and writes `run.json`;
  - a run directory may only be reused for the same kind of run;
  - Makefiles create no extra files and clean only their own outputs. Callers handle the rest, such as BBV files.
- **Judge runs by spike's outputs, never by make's exit status:** a program can exit non-zero legitimately, and spike stopped by SIGTERM exits 0.

## 4. Spike behaviour fwm relies on (check the spike source, not only its guide)

Verify formats and options in `riscv-isa-sim` / `riscv-fesvr` source before designing a feature; the user expects this.

**Minimum versions.** Use the newest installed toolchain with spike (`<toolchain>`; on 2026-10-20 it had riscv-isa-sim c303463 / riscv-fesvr f8a1a42).

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
- created for one hart only; checkpoints at or after the program's exit are silently not created (hence `repo compose add checkpoint`'s check against the description);
- spike creates checkpoints at the counts of a description file, `<name>: <count>`, and the run ends after the last one;
- a count of 0 is allowed, and `+create-checkpoint` can combine with `+load-checkpoint`, counting from the restore. That is a good way to compare states.

**Validation** (`repo compose set-checkpoint-validation`): a run from a checkpoint passes only on `target_exit` with the trace's per-hart `instret` and exit status. A replay stops at the first divergence, so reaching the end proves the output.

**Real runs aren't reproducible to the instruction.** A record or `from-sysroot` run gets its system calls answered by the host, and the program's path can depend on the answers. Measured on 2026-10-27, a small static glibc program took 1503320 instructions with its stdout to a file and 1503515 with it to `/dev/null`, with the same output. So never check a `from-sysroot` run's `instret` against the trace's; only replays (`from-fesvr-trace`, `from-checkpoint`, `from-simpoint`) repeat the recording exactly. A trace's own count is that of its record run.

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
- **Metadata:** an entry point has free-form `metadata`, replaced with `repo compose set-metadata`; `--exist-ok` ignores differences in it.
- **Checkpoint import:** `repo compose add checkpoint <trace> <desc> <checkpoint-dir> [--simpoint-info] [--sim-version]`. The SimPoint info is `{"interval", "maxK", "checkpoints": {name: {"index", "weight"}}}`; weights are never decoded from names.
- **Run generators:** `fwm mkgen` is for users; `fwm repo bootstrap mkgen` is for the creator and only reads the repository.
- **Old spikes:** old dumps and recordings without provenance are refused rather than supported. The repositories get rebuilt instead.
- **`--sim-version`:** `repo compose add trace` has none, because the provenance holds the recording spike's build. Checkpoints keep it, since their files carry no provenance.
- **Copying between repositories** (2026-10-20): one mechanism, `fwm/repo/transfer.py`, serves merging and subsets.
  - `repo pull SOURCE` writes `--repo-path`; `repo push DEST` reads it; `repo subrepo NEW` is `init` + `push`, and removes `NEW` on failure.
  - It picks entry points (or `--all`), each bringing its sysroot, traces and checkpoints (`--no-traces`, `--no-checkpoints`).
  - Rows are copied as they are, trace UUIDs included.
  - What the destination has is kept under the `--exist-ok` rules. A trace is matched by its recording, so a destination trace with another UUID is kept and the checkpoints attach to it.
  - Destination rows never change; there are no updates or hard links.
  - Conflicts are all checked first, and then nothing is copied.
  - Each entry point is read from the source in a short read transaction of its own (a long one would hold up the source's writers), then staged, checked to be exactly what its catalogs list, and committed on its own, so reruns complete an interrupted copy.
  - Before anything is copied, what was read is checked (`_check_source`): names and catalog paths as an import checks them (a source's database is not trusted), and that the entry points of a sysroot saw the same one. A sysroot the destination has is compared once per copy, except by the check made with the write lock held, which compares afresh: never trust a cache across a commit by row id, as SQLite reuses the largest id once its row is deleted.
  - Two repositories are open at once. The side that isn't `--repo-path` (pull's source, push's destination) is opened with `Repo.open(..., bind=False)`. During the copy the destination is bound to `database_proxy` (`Repo.bound()`, which binds back what was bound before), and the source is read through its `bind_ctx()`.
  - `subrepo` undoes a failed copy by removing what it created: the new directories, or only the content of an existing empty directory, which keeps its mode, group and ACLs. Directories it created are removed only while empty: another command may be using them.
  - `pull`'s completion reads its `SOURCE` argument, not `--repo-path`.

**Kept deliberately:**
- the Makefiles keep a minimal behaviour (no extra files, no BBV cleanup);
- there is no `spawn --catalog`;
- `check-fesvr-trace --json` has no `finish_reason` (a passing check always means `target_exit`).

## 6. Working with the user

- **Commits:** commit each logical step on its own, after its tests pass. Messages follow 50/72, with a lowercase imperative subject (e.g. "store each trace's provenance"), and end with the attribution trailer the session's instructions specify. Never push unless asked.
- **Plans:** non-trivial work goes through plan mode. The user reviews the plan and comments on it, sometimes several times. Plans and documents must be readable: plain words, short sentences, tables and lists, no jargon, no dense paragraphs. The user has rejected unreadable plans.
- **Ask, don't assume,** when a decision is the user's: behaviour changes, compatibility, what to delete. Offer a recommended option.
- **Spike bugs:** when fwm hits one, don't patch around it silently. Reproduce it with spike alone, and write a problem description with a reproduction for the riscv-isa-sim agent. Earlier ones are in `fwm-bootstrap/vcs_untracked/quicktest/mcf29-repro/PROBLEM.md` and `.../sigterm-repro/PROBLEM.md`. Then verify the fix the user installs.
- **The user edits files and history too.** If a file changed on disk, take it as intended. Commits may be rebased: check `git log` before assuming your hashes.

## 7. Development

```sh
uv run pytest                    # ~550 tests, ~45 s
uv run mypy fwm tests
uv run ruff check fwm tests      # pyproject pins ruff's former default rules (E4, E7, E9, F)
```

**Spike end-to-end test** (`tests/integration/test_spike_e2e.py`, ~45 s): it needs a helloworld sysroot built with the new toolchain. Scratch files vanish between sessions; rebuild it with:

```sh
R=<toolchain>                    # the newest one with spike
S=<scratch dir>; mkdir -p $S/sysroot/app $S/sysroot/boot
$R/bin/riscv64-unknown-linux-gnu-gcc -static -Wl,-Ttext-segment,0x10000 -O2 -o $S/sysroot/app/helloworld.rv64 \
    <fwm-bootstrap>/suites/helloworld/sysroots/000.helloworld/src/helloworld.c
cp $R/riscv64-unknown-elf/bin/pk $S/sysroot/boot/pk
echo '{"command": "helloworld.rv64", "working_directory": "/app", "envs": null, "stdin_file": null,
       "boot_elf": "/boot/pk", "mem_mb": 256, "nprocs": 1}' > $S/entry.json
FWM_TEST_SPIKE=$R/bin/spike FWM_TEST_SYSROOT=$S/sysroot FWM_TEST_ENTRY_POINT=$S/entry.json \
FWM_TEST_SPIKE_SRC=<riscv-isa-sim> uv run pytest tests/integration
```

**Building spike** (only if the installed one is older than needed):
- configure `<riscv-isa-sim>` with CMake (`-DCMAKE_BUILD_TYPE=RelO3 -DENABLE_DBG_TRACE=1`), out of tree;
- build `--target spike` only: other targets may need more than the checkout has;
- check `spike --version`'s commits against the checkout. The user usually installs new builds themselves.

**Pitfalls:**
- **Two environments:** a shell started in fwm-bootstrap may carry its `VIRTUAL_ENV`, so `uv` warns. Harmless.
- **No system Python on a cluster:** the login node's Python may be missing on compute nodes, so fwm runs on uv's Python, which lives on the shared filesystem. Startup speed has to come from importing less.
- **zsh word splitting:** the user's shell is zsh, so `$var` holding several words isn't split. Use bash for such loops.
- **Signals in tests:** wait for the whole process group to exit before reading a dump. make ends before spike finishes writing.
- **`tests/fesvr_files.py`** writes fake files that must match the real formats. When spike changes a format, change the fakes and the readers together.
