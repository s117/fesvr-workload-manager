# The interface between fwm and fwm-bootstrap

fwm-bootstrap is the workload preparation pipeline (Snakemake) that turns a workload into fwm repository content: a sysroot, entry points, a trace and checkpoints. This document is the contract it relies on: where each command runs, what it reads and writes, how it reports results, and how it behaves when rerun or interrupted.

## Where commands run

| Where | What runs there |
| --- | --- |
| One host, e.g. the Snakemake controller (`localrules`) | Commands that change the repository: `fwm repo init`, `repo add`, `repo set-metadata`, `repo remove`, `repo gc`, `repo record-validation`. |
| Any compute node | The generated Makefiles (`make -C <run dir> run`). Only the record and `from-sysroot` runs open the database, read-only, when `fwm spawn` checks the sysroot out. |
| Anywhere, even without write permission | `fwm list`, `show`, `verify`, `mkgen`, `repo bootstrap`, `spawn`, shell completion; `fwm check`, which needs no repository at all. |

The database uses SQLite's rollback journal, not WAL, so it can sit on a shared filesystem such as NFS and be read from several hosts. Commands that change it start their transaction with the write lock held and wait up to 30 seconds for each other. Keep them on one host anyway: SQLite's locking over a network filesystem is only as reliable as that filesystem's.

## Pipeline outline

```
 1. fwm repo add sysroot <dir> <sysroot> --exist-ok                                   controller
 2. fwm repo add entry <sysroot> <workload> <entry> <entry.json> --exist-ok           controller
    fwm repo set-metadata <entry> <metadata.json>         (only if the metadata changed)
 3. spike --version > V                                                                compute
    fwm repo bootstrap record <entry> -o R1
    make -C R1 run SIM_FLAGS_EXTRA=-s<interval> || true
    fwm check recording R1/trace --final-state R1/final-state.json      -> exit 4: cut short
    -> validate R1's console and sysroot with the suite's validator
 4. fwm repo add trace <entry> R1/trace --final-state R1/final-state.json \
        --sim-version V --exist-ok                                       -> trace UUID   controller
 5. SimPoint analysis of R1/bbv_proc_0.bb.gz -> description file D, SimPoint info S    pipeline
 6. fwm repo bootstrap create-checkpoints <uuid> D -o R3                             compute
    make -C R3 run || true
 7. fwm repo add checkpoint <uuid> R3/ckpt.desc R3/checkpoints \
        --simpoint-info S --sim-version V --exist-ok                     -> <uuid>:<name> per line   controller
 8. per checkpoint:                                                                    compute
    fwm mkgen from-checkpoint <uuid>:<name> -o R4
    make -C R4 run || true
    fwm repo record-validation <uuid>:<name> R4                          -> exit 0 passed, 4 failed   controller
```

- **Step 2: `entry.json`** holds the entry point's launch fields: `command`, `working_directory`, `envs` (`null` or `{}`), `stdin_file` (or `null`), `boot_elf`, `mem_mb`, and `nprocs` (must be 1). In paths, `/` is the sysroot's root and `.` the working directory. `boot_elf` and `stdin_file` must be regular files of the sysroot, not symlinks.
- **Step 2's metadata.** `entry.json` may also hold `metadata`, any JSON object: put the suite's `index.yaml` `metadata` there (and its `auxiliary` data, if runs need it). fwm keeps it, shows it, and copies it into every `run.json`, but doesn't interpret it. `--exist-ok` doesn't compare it, so a rerun keeps the stored metadata and warns if it differs; `fwm repo set-metadata` replaces it (or removes it, with a file holding `null`) without touching the traces.
- **Step 3** is the only step that needs the sysroot, and it must run on Linux. Steps 6 and 8 need only the trace and its checkpoints, so they can also run on ARM64 Linux or macOS.
- **Step 3's check.** `fwm check recording` runs the checks of `repo add trace` on the compute node, without a repository. A run cut short by the job's time limit fails there (spike stopped by SIGTERM writes its final state and exits 0), so the job can be retried with more time, instead of failing later in the import on the controller.
- **Step 3's SimPoint profile.** `-s<interval>` makes spike write `bbv_proc_<hart>.bb.gz` in the run directory. Taking it in the record run saves a full replay; a replay with `-s` gives the same profile. It needs a spike built without `NO_SIMPOINT`.
- **Step 5's description file.** A checkpoint's instruction count is the number of instructions retired before it. spike's BBV intervals are not exactly `<interval>` long (the first was 1,000,005 for a 1,000,000 interval), and spike drops the last partial interval. So the count of the interval with index `i` must be the sum of the sizes of intervals `0 … i-1`, not `i × interval`.
- **Step 5's names.** A checkpoint's name is just a name; fwm reads nothing from it. riscv-simenv's `<entry>.<simpoint index>.<weight>` (e.g. `bfs-road-n64.3.0.25`) stays readable. Leave out riscv-simenv's `.gz`: an fwm checkpoint isn't a gzip file (spike compresses the memory image inside it).
- **Step 5's SimPoint info**, made from the SimPoint v3.2 outputs, names exactly the checkpoints of D:
  ```json
  {"interval": 100000000, "maxK": 10,
   "checkpoints": {"bfs-road-n64.3.0.25": {"index": 3, "weight": 0.25312}, ...}}
  ```
  `interval` (> 0), `maxK` (> 0), and each checkpoint's `index` (≥ 0, distinct) and `weight` (in [0, 1]) are required; any other key is rejected. The weights needn't sum to 1, so a subset of the SimPoints can be imported.
- **Step 7** imports all the checkpoints of the description in one transaction. Each must be in `R3/checkpoints`, so a checkpoint spike didn't create fails the import: spike creates none at or after the program's exit, and reports nothing about it. Other files there are ignored.

## Running a Makefile

`fwm repo bootstrap ... -o <dir>` and `fwm mkgen ... -o <dir>` write `<dir>/Makefile` and `<dir>/run.json` (below), and print the Makefile's path on stdout. The directory must be new, empty, or a run directory fwm generated before for the same kind of run (e.g. `from-checkpoint`, of any checkpoint); another kind is refused (status 4), since its Makefile wouldn't remove the former outputs. Each Makefile has a single target, `run`, which removes that run's previous outputs, prepares what spike needs, and runs spike.

| Generated by | Outputs |
| --- | --- |
| `repo bootstrap record` | `trace/` (the recording), `final-state.json`, `console.stdout`, `console.stderr`, and the post-run `sysroot/` |
| `repo bootstrap create-checkpoints` | `ckpt.desc` (the copied description, written when generated), `final-state.json`, and `checkpoints/` |
| `mkgen from-sysroot` | `final-state.json`, `console.stdout`, `console.stderr`, and the post-run `sysroot/` |
| `mkgen from-fesvr-trace`, `from-checkpoint` | `final-state.json` |
| `mkgen from-simpoint` | `final-state.json`, left empty when `-e$(INTERVAL)` stops the run, as it normally does |

**What the calling script does:**
- **Run `make -C <dir> run || true`, and let fwm judge the run,** not make's exit status. A program that legitimately exits non-zero makes `make` fail, while spike stopped by SIGTERM (e.g. a job's time limit) exits 0. fwm judges each kind of run from its outputs:
  - a record run, by `fwm repo add trace --final-state`, or before that, on the node that ran it, by `fwm check recording --final-state`;
  - a create-checkpoints run, by `fwm repo add checkpoint`;
  - a run from a checkpoint, by `fwm repo record-validation`.
- **Save `spike --version`** for provenance, and pass the file to `repo add trace` and `repo add checkpoint` with `--sim-version`. fwm checks it is build information. A recording replays reliably only with a spike of the same semantics, and this records which one made it.
- **Clean what the Makefile doesn't own,** such as BBV files, or use a fresh run directory.

**`final-state.json`** is written when the simulation ends: normally, or when spike is stopped by SIGTERM or SIGINT, with the instruction count where it stopped. It stays empty after an `-e` stop or a simulator error, such as a replay that diverged from its recording. What it holds:
- **record, from-sysroot, from-fesvr-trace, from-checkpoint:** each HART's instruction count at the end of the program, or where a signal stopped it.
- **create-checkpoints:** where the run stopped, just after the last checkpoint, not the end of the program. Don't compare it with the trace.

Each Makefile also starts with a line `# fwm-run: {...}` saying what the run is for, e.g. `{"kind": "from-checkpoint", "trace": "<uuid>", "checkpoint": "<name>"}`. `record-validation` uses it to check that a run directory belongs to the checkpoint.

### run.json

**`run.json` describes what the run uses,** so its results can be decoded without the repository. The Makefile's `run` target leaves it alone. It is a snapshot taken when `mkgen` ran: a later validation or `set-metadata` shows in `fwm show`, not here. It holds:
- **`kind`:** the command that generated it: `from-sysroot`, `from-fesvr-trace`, `from-checkpoint`, `from-simpoint`, `record` or `create-checkpoints`.
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
| `fwm repo add sysroot` / `add entry`, `repo set-metadata` | the name |
| `fwm repo add trace` | the trace UUID |
| `fwm check recording` | `complete: …` with the exit status and instret; with `--json`, `{complete, exit_status, mem_mb, num_harts, boot_elf_sha256, harts: [{hart_id, recording_sha256, num_commands, instret}]}` |
| `fwm repo add checkpoint` | `<trace UUID>:<name>`, one line per checkpoint, in the order of the description |
| `fwm repo record-validation` | `passed: <why>` or `failed: <why>` |
| `fwm repo verify` | one line per problem: `<kind> <id>: <path>: <reason>`, or `leftover <path>: <reason>` |
| `fwm repo gc` | one line per leftover removed (with `--dry-run`, found): `<path>: <reason>` |
| `fwm mkgen ...`, `fwm repo bootstrap ...` | the Makefile path |
| `fwm list ... --json`, `fwm show ... --json` | JSON, below |

Exit status:

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | unexpected error |
| 2 | usage error, including an unusable repository path: not a repository, another schema version, or read-only for a command that changes it |
| 3 | not found |
| 4 | failed check: invalid input, a recording that doesn't match its entry point, a checkpoint that doesn't belong to its trace, a failed validation, problems found by `verify` |
| 5 | already exists (see Reruns) |

### JSON

`fwm show <kind> <id> --json` prints one object; `fwm list <kinds> --json` prints a list of the same objects. Times are ISO 8601 with a timezone, and absent values are `null`.

- **workload:** `name`, `creation_time`, `entries` (names). A workload has no description: describe it in its entry points' `metadata`.
- **sysroot:** `name`, `description`, `creation_time`, `path`, `num_files`, `num_links`, `num_dirs`, `size_bytes`, `entries`.
- **entry:** `name`, `workload`, `sysroot`, `description`, `creation_time`, `command`, `working_directory`, `stdin_file`, `boot_elf`, `mem_mb`, `nprocs`, `metadata` (the object given, or `null`), `traces` (UUIDs).
- **trace:**
  - `uuid`, `entry`, `description`, `creation_time`, `path`;
  - `mem_mb`, `num_harts`, `boot_elf_sha256`;
  - `exit_status` (0–255, `null` if truncated) and `complete`;
  - `sim_version`: `{"builds": {<component>: {<field>: <value>}}, "raw": <text>}`, or `null`. The components are `spike` and `FESVR (libfesvr)`, with fields such as `Version` and `Commit`;
  - `fwm_version`;
  - `harts`: a list of `{hart_id, recording_sha256, num_commands, instret}`;
  - `checkpoints` (names).
- **checkpoint:**
  - `name`, `trace`, `entry`, `description`, `creation_time`, `path`;
  - `format_version`, `content_sha256`;
  - `simpoint_weight`, `simpoint_index`, `simpoint_interval`, `simpoint_max_k` (all `null` without SimPoint info), `requested_count`;
  - `validation`: `{"status": "passed" | "failed", "time", "detail"}`, or `null` if not validated;
  - `sim_version`, `fwm_version`, as for a trace;
  - `harts`: a list of `{hart_id, recording_sha256, cmd_index, hart_state_version, csr_count}`. `csr_count` is the instruction count where the checkpoint was actually taken; it can differ slightly from `requested_count`.

`content_sha256` is the SHA-256 of what `riscv-isa-sim/scripts/checkpoint-digest.py <file>` prints. It doesn't depend on how zlib compressed the memory, so two checkpoints of the same point compare equal wherever they were created.

## Reruns

Every `repo add` takes `--exist-ok`, so a rule can simply be rerun:

| Adding | With `--exist-ok`, succeeds and prints the existing item when |
| --- | --- |
| a sysroot | the tree has the same files, links, directories, permissions and digests |
| an entry point | its sysroot, workload and launch fields are the same (the description and metadata may differ; the stored ones are kept) |
| a trace | the recording is the same (per-HART SHA-256) and so is the final state |
| a checkpoint | the trace has a checkpoint of that name with the same content digest |

A different item under the same name fails with status 5. Without `--exist-ok`, adding anything that exists fails with status 5. A recording that already is a trace of the entry point counts as existing, so importing it twice never creates a second trace.

`repo record-validation` can be rerun; the last result is kept.

## Interruptions

- **SIGTERM and SIGHUP,** which batch schedulers send to stop a job, end fwm through its cleanup, like Ctrl-C: an interrupted import leaves nothing behind, and an interrupted removal leaves the repository as it was.
- **A harder kill** (SIGKILL, a node failure) can leave a staging copy, or files that no row refers to. A removal deletes its files only after committing, so a kill never leaves rows whose files are missing.
- **`fwm repo verify`** without arguments reports such leftovers, and **`fwm repo gc`** removes them. A staging copy counts as a leftover only when no process holds its lock and it is older than ten minutes, so gc can run while imports do.

## Validating a checkpoint

A replay stops at the first difference from its recording: a different command, different data written to the host, or a recording that ran out. A checkpoint is therefore validated by loading it and running to the end of the program, without `-e`.

`fwm repo record-validation <checkpoint> <run dir>`:
- **Passes when** each HART's final instruction count in `final-state.json` equals the trace's. A run that reached the trace's final count has replayed the recorded exit, so it has the trace's exit status too.
- **Fails** otherwise, with the reason: no `final-state.json`, an empty one (an `-e` stop or a simulator error), or different counts (e.g. spike was stopped by a signal).
- **Is refused** (status 4, nothing recorded) when the run directory isn't a `mkgen from-checkpoint` run of that checkpoint, or the trace has no final instruction counts (imported without `--final-state`, or truncated).

## Truncated recordings

A recording is truncated when its run was stopped before the program exited:
- by `-e`: spike leaves `final-state.json` empty, which `fwm repo add trace --final-state` rejects;
- by a signal (SIGTERM, SIGINT): spike writes `final-state.json` and exits 0, and `fwm repo add trace --final-state` rejects the combination of a truncated recording and a final state;
- import a truncated recording on purpose without `--final-state`;
- the trace then has no instruction counts, so `repo bootstrap create-checkpoints` can't check the counts against the end of the recording, and its checkpoints can't be validated.

## 721sim

Checkpoints are created with spike. A 721sim build can load them only if it carries the same riscv-fesvr as the spike that made them: one that saves `boot.elf` in recordings, has the versioned checkpoint format and the portable `RVHSTATE` hart state. The `from-checkpoint` and `from-simpoint` Makefiles run 721sim with `make -C <run dir> run SIM=<721sim>`.
