# The interface between fwm and fwm-bootstrap

fwm-bootstrap is the workload preparation pipeline (Snakemake) that turns a workload into fwm repository content: a sysroot, entry points, a trace and checkpoints. This document is the contract it relies on: the commands it runs, what they read and write, and how they report results.

## Pipeline outline

```
1. fwm repo add sysroot <dir> <sysroot>                        once per sysroot
2. fwm repo add entry <sysroot> <workload> <entry> <json>      once per entry point
3. fwm mkgen record <entry> -o R1 && make -C R1 run            record; validate R1's outputs
4. fwm repo add trace <entry> R1/trace --final-state R1/final-state.json      -> trace UUID
5. fwm mkgen replay <uuid> -o R2 && make -C R2 run SIM_FLAGS_EXTRA=-s<interval>   SimPoint BBV, from the trace
   (SimPoint analysis -> checkpoint description file D)
6. fwm mkgen replay <uuid> --create-checkpoints D -o R3 && make -C R3 run
7. fwm repo add checkpoint <uuid> R3/checkpoints/<name>        once per checkpoint
8. fwm mkgen load <uuid>:<name> -o R4 && make -C R4 run        validate each checkpoint
```

- **Step 2: `<json>`.** The entry point's launch fields: `command`, `working_directory`, `envs` (`null` or `{}`), `stdin_file` (or `null`), `boot_elf`, `mem_mb`, and `nprocs` (must be 1). In paths, `/` is the sysroot's root and `.` the working directory. `boot_elf` and `stdin_file` must be regular files of the sysroot, not symlinks.
- **Step 3.** Recording is the only step that needs the sysroot, and it must run on Linux.
- **Step 4.** A truncated recording (one whose program never exited) is accepted, with a warning. It can't be replayed past its last command, and its checkpoints can't be created or loaded past it. See [Truncated recordings](#truncated-recordings) for the `--final-state` rule.
- **Steps 5–8** need only the trace and its checkpoints, so they can run on another host, including ARM64 Linux or macOS.

## Names

| Name | Characters | Unique within |
| --- | --- | --- |
| sysroot, workload, entry point | `[A-Za-z0-9._-]` | the repository |
| checkpoint | `[A-Za-z0-9._-]`, the rule of FESVR's description file | its trace |

A SimPoint checkpoint should be named `<entry>.<simpoint index>.<weight>` with the weight to 2 decimals, e.g. `bfs-road-n64.3.0.25`. `fwm repo add checkpoint` then decodes its weight, unless `--simpoint-weight` is given. A name that doesn't follow the convention gets no weight.

## Run directories

`fwm mkgen ... -o <dir>` writes `<dir>/Makefile`, and prints its path on stdout. The directory must be new, empty, or a run directory fwm generated before. Each Makefile has the single target `run`, which removes that run's previous outputs first, so a failed job can simply be run again.

Outputs, at fixed names in the run directory:

| Makefile | Outputs |
| --- | --- |
| record | `trace/` (the recording), `final-state.json`, `console.stdout`, `console.stderr`, and the post-run `sysroot/` |
| replay | `final-state.json` |
| replay `--create-checkpoints` | `ckpt.desc` (the copied description), `final-state.json`, and `checkpoints/`, which holds only the checkpoint files |
| load | `final-state.json` |

- **The suite validator.** It gets the record run's console output (`console.stdout`, `console.stderr`) and its post-run `sysroot/`. A replay prints nothing and writes no file, so only the record run can be validated by its output.
- **SimPoint profile.** With `SIM_FLAGS_EXTRA=-s<interval>`, spike writes `bbv_proc_<hart>.bb.gz` in the run directory. This works in a replay too, so the profile can be taken from the trace. It needs a spike built without `NO_SIMPOINT`. Don't combine `-s` with `--create-checkpoints`: the BBV would be written to `checkpoints/`.
- **Checkpoint stop points.** A checkpoint is taken *before* the instruction count in the description file, counted from the start of the run. A count at or beyond the trace's final `instret` is rejected by `fwm mkgen replay`.

## Command results

**stdout carries results, stderr carries messages.** Logs and errors go to stderr only.

| Command | stdout |
| --- | --- |
| `fwm repo init <path>` | the repository path |
| `fwm repo add sysroot` / `add entry` | the name |
| `fwm repo add trace` | the trace UUID |
| `fwm repo add checkpoint` | `<trace UUID>:<name>` |
| `fwm mkgen ...` | the Makefile path |
| `fwm repo verify` | one line per problem: `<kind> <id>: <path>: <reason>` |
| `fwm list ... --json`, `fwm show ... --json` | JSON (below) |

Exit status:

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | unexpected error |
| 2 | usage error |
| 3 | not found |
| 4 | failed check, e.g. an invalid input, a name already used, a recording that doesn't match its entry point, or a checkpoint that doesn't belong to its trace |

### JSON

`fwm show <kind> <id> --json` prints one object. `fwm list <kinds> --json` prints a list of the same objects. Times are ISO 8601 with a timezone, and absent values are `null`.

- **workload:** `name`, `description`, `creation_time`, `entries` (names).
- **sysroot:** `name`, `description`, `creation_time`, `path`, `num_files`, `num_links`, `num_dirs`, `size_bytes`, `entries`.
- **entry:** `name`, `workload`, `sysroot`, `description`, `creation_time`, `command`, `working_directory`, `stdin_file`, `boot_elf`, `mem_mb`, `nprocs`, `traces` (UUIDs).
- **trace:**
  - `uuid`, `entry`, `description`, `creation_time`, `path`;
  - `mem_mb`, `num_harts`, `boot_elf_sha256`;
  - `exit_status` (`null` if truncated) and `complete`;
  - `harts`: a list of `{hart_id, recording_sha256, num_commands, instret}`;
  - `checkpoints` (names).
- **checkpoint:**
  - `name`, `trace`, `entry`, `description`, `creation_time`, `path`;
  - `format_version`, `content_sha256`, `simpoint_weight`;
  - `harts`: a list of `{hart_id, recording_sha256, cmd_index, hart_state_version, csr_count}`. `csr_count` is the instruction count where the checkpoint was actually taken. It can differ slightly from the requested count.

`content_sha256` is the SHA-256 of what `riscv-isa-sim/scripts/checkpoint-digest.py <file>` prints. It doesn't depend on how zlib compressed the memory, so two checkpoints of the same point compare equal wherever they were created.

## Concurrency

Jobs may run fwm commands on one repository at the same time:
- The database runs in WAL mode, and every transaction takes the write lock when it starts, with a 30-second busy timeout.
- An import is copied into the repository's staging area first, then renamed into place within the transaction that records it, so a failed import leaves nothing behind.
- Two imports of the same name: one succeeds, and the other fails with status 4.

## Validating a checkpoint

A replay stops at the first difference from its recording: a different command, different data written to the host, or a recording that ran out. A checkpoint is therefore validated by loading it and running to the end. The run must succeed, and each HART's final `instret` must equal the trace's (`fwm show trace <uuid> --json`, `harts[].instret`). This applies to complete traces only.

## Truncated recordings

If `-e` stops the recording run:
- spike leaves `final-state.json` **empty**, because it writes the file only when the program ends;
- `fwm repo add trace --final-state` rejects that empty file, so import such a recording without `--final-state`;
- the trace then has no `instret`, and fwm can't check its checkpoint counts against the end of the recording.
