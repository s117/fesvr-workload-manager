# The interface between fwm and fwm-bootstrap

fwm-bootstrap is the workload preparation pipeline (Snakemake) that turns a workload into fwm repository content: a sysroot, entry points, a trace and checkpoints. This document is the contract it relies on: where each command runs, what it reads and writes, how it reports results, and how it behaves when rerun or interrupted.

## Where commands run

| Where | What runs there |
| --- | --- |
| One host, e.g. the Snakemake controller (`localrules`) | Commands that change the repository: `fwm repo init`, `repo gc`, `repo pull`, and every `repo compose` command (`add`, `remove`, `set-metadata`, `set-checkpoint-validation`); and `repo push` / `repo subrepo`, for the repository they write. |
| Any compute node | The generated Makefiles (`make -C <run dir> run`). Only the record and `from-sysroot` runs open the database, read-only, when `fwm spawn` checks the sysroot out. |
| Anywhere, even without write permission | `fwm list`, `show`, `mkgen`, `spawn`, `repo verify`, shell completion, and every `repo bootstrap` command: `mkgen record-fesvr-trace`, `mkgen create-checkpoints`, and `check-fesvr-trace`, which needs no repository at all. A repository is only read when it is copied from (`repo pull`'s source, `repo push`'s and `repo subrepo`'s `--repo-path`). |

The database uses SQLite's rollback journal, not WAL, so it can sit on a shared filesystem such as NFS and be read from several hosts. Commands that change it start their transaction with the write lock held and wait up to 30 seconds for each other. Keep them on one host anyway: SQLite's locking over a network filesystem is only as reliable as that filesystem's.

## Pipeline outline

```
 1. fwm repo compose add sysroot <dir> <sysroot> --exist-ok                                 controller
 2. fwm repo compose add entry <sysroot> <workload> <entry> <entry.json> --exist-ok         controller
    fwm repo compose set-metadata <entry> <metadata.json>     (only if the metadata changed)
 3. <the run's spike> --version > V                                                       compute
    fwm repo bootstrap mkgen record-fesvr-trace <entry> -o R1
    make -C R1 run SIM_FLAGS_EXTRA=-s<interval> || true
    fwm repo bootstrap check-fesvr-trace R1/trace --final-state R1/final-state.json  -> exit 4: cut short
    -> validate R1's console and sysroot with the suite's validator
 4. fwm repo compose add trace <entry> R1/trace --final-state R1/final-state.json \
        --exist-ok                                                  -> trace UUID          controller
 5. SimPoint analysis of R1/bbv_proc_0.bb.gz -> description file D, SimPoint info S        pipeline
 6. fwm repo bootstrap mkgen create-checkpoints <uuid> D -o R2                             compute
    make -C R2 run || true
 7. fwm repo compose add checkpoint <uuid> R2/ckpt.desc R2/checkpoints \
        --simpoint-info S --sim-version V --exist-ok                -> <uuid>:<name> per line  controller
 8. per checkpoint:                                                                        compute
    fwm mkgen from-checkpoint <uuid>:<name> -o R3
    make -C R3 run || true
    fwm repo compose set-checkpoint-validation <uuid>:<name> R3    -> exit 0 passed, 4 failed  controller
```

- **Step 2: `entry.json`** holds the entry point's launch fields: `command`, `working_directory`, `envs` (`null` or `{}`), `stdin_file` (or `null`), `boot_elf`, `mem_mb`, `nprocs` (must be 1), and optionally `copy_spawn` (default `true`). In paths, `/` is the sysroot's root and `.` the working directory. `boot_elf` and `stdin_file` must be regular files of the sysroot, not symlinks.
- **Step 2's `copy_spawn`** decides how the record run gets the sysroot (`fwm spawn --mode auto`): `true` copies it; `false` links each file to the repository's read-only file, which takes no time or space, but the program can't open those files for writing, and `stat` reports them read-only. It is a launch field: `--exist-ok` compares it.
- **Step 2's metadata.** `entry.json` may also hold `metadata`, any JSON object: put the suite's `index.yaml` `metadata` there (and its `auxiliary` data, if runs need it). fwm keeps it, shows it, and copies it into every `run.json`, but doesn't interpret it. `--exist-ok` doesn't compare it, so a rerun keeps the stored metadata and warns if it differs; `fwm repo compose set-metadata` replaces it (or removes it, with a file holding `null`) without touching the traces.
- **Step 3** is the only step that needs the sysroot, and it must run on Linux. Steps 6 and 8 need only the trace and its checkpoints, so they can also run on ARM64 Linux or macOS.
- **Step 3's check.** `fwm repo bootstrap check-fesvr-trace` runs the checks of `repo compose add trace` on the compute node, without a repository. A run cut short by the job's time limit fails there (its final state says `interrupted`, and spike exits 0), so the job can be retried with more time, instead of failing later in the import on the controller.
- **Step 3's SimPoint profile.** `-s<interval>` makes spike write `bbv_proc_<hart>.bb.gz` in the run directory. Taking it in the record run saves a full replay; a replay with `-s` gives the same profile. It needs a spike built without `NO_SIMPOINT`.
- **Step 5's description file.** A checkpoint's instruction count is the number of instructions retired before it. spike's BBV intervals are not exactly `<interval>` long (the first was 1,000,005 for a 1,000,000 interval), and spike drops the last partial interval. So the count of the interval with index `i` must be the sum of the sizes of intervals `0 … i-1`, not `i × interval`.
- **Step 5's names.** A checkpoint's name is just a name; fwm reads nothing from it. riscv-simenv's `<entry>.<simpoint index>.<weight>` (e.g. `bfs-road-n64.3.0.25`) stays readable. Leave out riscv-simenv's `.gz`: an fwm checkpoint isn't a gzip file (spike compresses the memory image inside it).
- **Step 5's SimPoint info**, made from the SimPoint v3.2 outputs, names exactly the checkpoints of D:
  ```json
  {"interval": 100000000, "maxK": 10,
   "checkpoints": {"bfs-road-n64.3.0.25": {"index": 3, "weight": 0.25312}, ...}}
  ```
  `interval` (> 0), `maxK` (> 0), and each checkpoint's `index` (≥ 0, distinct) and `weight` (in [0, 1]) are required; any other key is rejected. The weights needn't sum to 1, so a subset of the SimPoints can be imported.
- **Step 7** imports all the checkpoints of the description in one transaction. Each must be in `R2/checkpoints`, so a checkpoint spike didn't create fails the import: spike creates none at or after the program's exit, and reports nothing about it. Other files there are ignored.

## Running a Makefile

`fwm repo bootstrap mkgen ... -o <dir>` and `fwm mkgen ... -o <dir>` write `<dir>/Makefile` and `<dir>/run.json` (below), and print the Makefile's path on stdout. The directory must be new, empty, or a run directory fwm generated before for the same kind of run (e.g. `from-checkpoint`, of any checkpoint); another kind is refused (status 4), since its Makefile wouldn't remove the former outputs. Each Makefile has a single target, `run`, which removes that run's previous outputs, prepares what spike needs, and runs spike.

| Generated by | Outputs |
| --- | --- |
| `repo bootstrap mkgen record-fesvr-trace` | `trace/` (the recording), `final-state.json`, `console.stdout`, `console.stderr`, and the post-run `sysroot/` |
| `repo bootstrap mkgen create-checkpoints` | `ckpt.desc` (the copied description, written when generated), `final-state.json`, and `checkpoints/` |
| `mkgen from-sysroot` | `final-state.json`, `console.stdout`, `console.stderr`, and the post-run `sysroot/` |
| `mkgen from-fesvr-trace`, `from-checkpoint` | `final-state.json` |
| `mkgen from-simpoint` | `final-state.json`, normally saying `instret_limit`: `-e$(INTERVAL)` stopped the run |

**What the calling script does:**
- **Run `make -C <dir> run || true`, and let fwm judge the run,** not make's exit status. A program that legitimately exits non-zero makes `make` fail, while spike stopped by SIGTERM (e.g. a job's time limit) exits 0. fwm judges each kind of run from its outputs:
  - a record run, by `fwm repo compose add trace --final-state`, or before that, on the node that ran it, by `fwm repo bootstrap check-fesvr-trace --final-state`;
  - a create-checkpoints run, by `fwm repo compose add checkpoint`;
  - a run from a checkpoint, by `fwm repo compose set-checkpoint-validation`.
- **Provenance.** A recording says how it was made, in its `recording_provenance.json` (riscv-isa-sim fd01c25 / riscv-fesvr 8472993 or later): when it started and finished, the recording machine (`uname`), the simulator's command line and working directory, and the spike's and FESVR's builds. fwm requires it, and keeps it with the trace. Checkpoint files say nothing of how they were made: **save `spike --version`** and pass the file to `repo compose add checkpoint` with `--sim-version`; fwm checks it is build information. A recording replays reliably only with a spike of the same semantics, and these record which one made it.
- **Clean what the Makefile doesn't own,** such as BBV files, or use a fresh run directory.

**`final-state.json`** (spike's `+final-state-dump`) says why the run finished, and each HART's instruction count there. spike writes it as `unfinished` when the run starts and replaces it at the end, so it is never empty. fwm requires this format: a dump without `finish_reason`, from a spike older than riscv-isa-sim eb87256 / riscv-fesvr 6f2d3b8, is refused, since such a spike's count at the program's exit depended on where the run started. Use riscv-isa-sim 872d191 / riscv-fesvr ecc3df3 or later: an earlier spike stopped by SIGTERM twice (under make, a scheduler's time limit or `kill -- -<pgid>`, which send it two) could leave an empty or broken dump; these ignore the repeat and write the dump atomically.

| `finish_reason` | The run finished because | Extra key | `hart_state` |
| --- | --- | --- | --- |
| `target_exit` | the program exited | `exit_code` | the count before the exit's `tohost` write, whatever the run started from |
| `checkpoints_created` | the last checkpoint was created | | at the last checkpoint, not the end of the program |
| `instret_limit` | `-e` stopped it | | the start count plus the limit |
| `debug_quit` | spike's debug mode was quit | | where it quit |
| `interrupted` | SIGTERM or SIGINT (e.g. a job's time limit); spike exits 0 | `signal` | where it stopped |
| `error` | the FESVR failed, e.g. a replay that diverged from its recording | `error`: the message | none |
| `unfinished` | spike died otherwise: a crash, an assert, `kill -9` | | none |

fwm takes a trace's counts only from a `target_exit` dump whose `exit_code` matches the recording's exit status, and validates a checkpoint only by a `target_exit` run (below).

Each Makefile also starts with a line `# fwm-run: {...}` saying what the run is for, e.g. `{"kind": "from-checkpoint", "trace": "<uuid>", "checkpoint": "<name>"}`. `repo compose set-checkpoint-validation` uses it to check that a run directory belongs to the checkpoint.

### run.json

**`run.json` describes what the run uses,** so its results can be decoded without the repository. The Makefile's `run` target leaves it alone. It is a snapshot taken when `mkgen` ran: a later validation or `repo compose set-metadata` shows in `fwm show`, not here. It holds:
- **`kind`:** which command generated it: `from-sysroot`, `from-fesvr-trace`, `from-checkpoint`, `from-simpoint` (`fwm mkgen`), `record` (`repo bootstrap mkgen record-fesvr-trace`) or `create-checkpoints` (`repo bootstrap mkgen create-checkpoints`). These names don't follow renamed commands, so older run directories keep working.
- **`generated`:** `time`, `command` (the generating command line), `fwm_version`, `repo` (the repository path) and `sim` (the Makefile's default `SIM`).
- **`sysroot`, `workload`, `entry`:** always. The entry point's `metadata` is here.
- **`trace`:** for every kind but `from-sysroot` and `record`.
- **`checkpoint`:** for `from-checkpoint` and `from-simpoint`.
- **`requested_checkpoints`:** for `create-checkpoints`, a list of `{name, count}`, the lines of `ckpt.desc`.

The sections are the objects of `fwm show --json` (see JSON below), without their lists of other items: the sysroot's and workload's `entries`, the entry point's `traces` and the trace's `checkpoints`.

## Names

| Name | Characters | Unique within |
| --- | --- | --- |
| sysroot, workload, entry point | `[A-Za-z0-9._-]` | the repository |
| checkpoint | `[A-Za-z0-9._-]`, not `.` or `..` | its trace |

- **A trace** is named by its UUID (a unique prefix is enough), or by the name of an entry point that has one trace.
- **A checkpoint** is named `<trace>:<name>`, or by its name alone when no other trace has a checkpoint of that name.

## Command results

**stdout carries results; stderr carries messages.** Logs, warnings and errors go to stderr only.

| Command | stdout |
| --- | --- |
| `fwm repo init <path>` | the repository path |
| `fwm repo compose add sysroot` / `add entry`, `repo compose set-metadata` | the name |
| `fwm repo compose add trace` | the trace UUID |
| `fwm repo bootstrap check-fesvr-trace` | `complete: …` with the exit status and instret; with `--json`, `{complete, exit_status, mem_mb, num_harts, boot_elf_sha256, harts: [{hart_id, recording_sha256, num_commands, instret}]}` |
| `fwm repo compose add checkpoint` | `<trace UUID>:<name>`, one line per checkpoint, in the order of the description |
| `fwm repo compose set-checkpoint-validation` | `passed: <why>` or `failed: <why>` |
| `fwm repo verify` | one line per problem: `<kind> <id>: <path>: <reason>`, or `leftover <path>: <reason>` |
| `fwm repo gc` | one line per leftover removed (with `--dry-run`, found): `<path>: <reason>` |
| `fwm repo pull`, `repo push` | one line per item, once its entry point is committed (an item several entry points share, like their sysroot, only once): `added\|present sysroot <name>`, `… entry <name>`, `… trace <UUID>` (a trace kept under another UUID: `present trace <its UUID> (<the copied UUID>)`), `… checkpoint <trace UUID>:<name>` |
| `fwm repo subrepo` | the new repository's path |
| `fwm mkgen ...`, `fwm repo bootstrap mkgen ...` | the Makefile path |
| `fwm list ... --json` | a JSON array of names: traces by UUID, checkpoints as `fwm show checkpoint` takes them (`<name>`, or `<trace UUID>:<name>` when other checkpoints have that name) |
| `fwm show ... --json` | JSON, below |

Exit status:

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | unexpected error |
| 2 | usage error, including an unusable repository path: not a repository, another schema version, or read-only for a command that changes it |
| 3 | not found |
| 4 | failed check: invalid input, a recording that doesn't match its entry point, a checkpoint that doesn't belong to its trace, a failed validation, problems found by `verify`. Also, from `repo pull` / `push` / `subrepo`, a repository changed by another command while it was read or copied to ("changed while … : copy again" / "run it again"): running it again can succeed |
| 5 | already exists: an item of the repository (see Reruns), or a destination (`spawn` without `--force`, `repo init` or `repo subrepo` into a non-empty directory) |

### JSON

`fwm show <kind> <id> --json` prints one object; to read many, list their names with `fwm list <kinds> --json` and show each. Times are ISO 8601 with a timezone, and absent values are `null`.

- **workload:** `name`, `creation_time`, `entries` (names). A workload has no description: describe it in its entry points' `metadata`.
- **sysroot:** `name`, `description`, `creation_time`, `path`, `num_files`, `num_links`, `num_dirs`, `size_bytes`, `entries`.
- **entry:** `name`, `workload`, `sysroot`, `description`, `creation_time`, `command`, `working_directory`, `stdin_file`, `boot_elf`, `mem_mb`, `nprocs`, `copy_spawn`, `metadata` (the object given, or `null`), `traces` (each `{uuid, checkpoints}`: a trace's UUID, and its checkpoints' names, ordered as in `show trace`).
- **trace:**
  - `uuid`, `entry`, `description`, `creation_time`, `path`;
  - `mem_mb`, `num_harts`, `boot_elf_sha256`;
  - `exit_status` (0–255, `null` if truncated) and `complete`;
  - `provenance`: the recording's `recording_provenance.json`, as recorded: `started`, `finished` (absent if the recording process died), `time_zone`, `uname` (`sysname`, `nodename`, `release`, `version`, `machine`), `command` (`argv`, `cwd`), `simulator` (`name`, `build`), and `fesvr`; the builds have fields such as `version`, `commit`, `repository` and `build_date`;
  - `fwm_version`;
  - `harts`: a list of `{hart_id, recording_sha256, num_commands, instret}`;
  - `checkpoints` (names).
- **checkpoint:**
  - `name`, `trace`, `entry`, `description`, `creation_time`, `path`;
  - `format_version`, `content_sha256`;
  - `simpoint_weight`, `simpoint_index`, `simpoint_interval`, `simpoint_max_k` (all `null` without SimPoint info), `requested_count`;
  - `validation`: `{"status": "passed" | "failed", "time", "detail"}`, or `null` if not validated;
  - `sim_version`: from `--sim-version`, `{"builds": {<component>: {<field>: <value>}}, "raw": <text>}`, or `null`. The components are `spike` and `FESVR (libfesvr)`, with fields such as `Version` and `Commit`;
  - `fwm_version`;
  - `harts`: a list of `{hart_id, recording_sha256, cmd_index, hart_state_version, csr_count}`. `csr_count` is the instruction count where the checkpoint was actually taken; it can differ slightly from `requested_count`.

`content_sha256` is the SHA-256 of what `riscv-isa-sim/scripts/checkpoint-digest.py <file>` prints. It doesn't depend on how zlib compressed the memory, so two checkpoints of the same point compare equal wherever they were created.

## Reruns

Every `repo compose add` takes `--exist-ok`, so a rule can simply be rerun:

| Adding | With `--exist-ok`, succeeds and prints the existing item when |
| --- | --- |
| a sysroot | the tree has the same files, links, directories, permissions and digests |
| an entry point | its sysroot, workload and launch fields (including `copy_spawn`) are the same (the description and metadata may differ; the stored ones are kept) |
| a trace | the recording is the same (per-HART SHA-256) and so is the final state |
| a checkpoint | the trace has a checkpoint of that name with the same content digest |

A different item under the same name fails with status 5. Without `--exist-ok`, adding anything that exists fails with status 5. A recording that already is a trace of the entry point counts as existing, so importing it twice never creates a second trace.

`repo compose set-checkpoint-validation` can be rerun; the last result is kept.

## Interruptions

- **SIGTERM and SIGHUP,** which batch schedulers send to stop a job, end fwm through its cleanup, like Ctrl-C: an interrupted import leaves nothing behind, and an interrupted removal leaves the repository as it was.
- **A harder kill** (SIGKILL, a node failure) can leave a staging copy, or files that no row refers to. A removal deletes its files only after committing, so a kill never leaves rows whose files are missing.
- **`fwm repo verify`** without arguments reports such leftovers, and **`fwm repo gc`** removes them. An import or copy committing at the moment `verify` runs can show as leftovers too: check again before acting on them. A staging copy counts as a leftover only when no process holds its lock and it is older than ten minutes, so gc can run while imports do.

## Validating a checkpoint

A replay stops at the first difference from its recording: a different command, different data written to the host, or a recording that ran out. A checkpoint is therefore validated by loading it and running to the end of the program, without `-e`.

`fwm repo compose set-checkpoint-validation <checkpoint> <run dir>`:
- **Passes when** `final-state.json` says `target_exit`, with the trace's exit status and each HART's final instruction count of the trace. The run has then replayed the rest of the program exactly, up to its recorded exit.
- **Fails** otherwise, with the reason: no `final-state.json`, a run that ended otherwise (its `finish_reason`, e.g. `error` with the simulator's message, `interrupted`, `instret_limit`), or different counts or exit status.
- **Is refused** (status 4, nothing recorded) when the run directory isn't a `mkgen from-checkpoint` run of that checkpoint, or the trace has no final instruction counts (imported without `--final-state`, or truncated).

## Truncated recordings

A recording is truncated when its run was stopped before the program exited: by `-e` (`instret_limit`), by a signal (`interrupted`), or by a simulator `error`. `fwm repo compose add trace --final-state` refuses such a run's dump, saying why the run finished: it takes only `target_exit`.

To import a truncated recording anyway, leave out `--final-state`. The trace then has no instruction counts, so `repo bootstrap mkgen create-checkpoints` can't check the requested counts against the end of the recording, and its checkpoints can't be validated.

## 721sim

Checkpoints are created with spike. A 721sim build can load them only if it carries the same riscv-fesvr as the spike that made them: one that saves `boot.elf` in recordings, has the versioned checkpoint format and the portable `RVHSTATE` hart state. The `from-checkpoint` and `from-simpoint` Makefiles run 721sim with `make -C <run dir> run SIM=<721sim>`.
