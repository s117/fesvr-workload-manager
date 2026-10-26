import os

import click

from fwm.cli.completion import complete_by_kind, complete_other_entry, complete_source_entry
from fwm.cli.fwm import open_repo, SectionedGroup
from fwm.cli.subcmds.bootstrap import bootstrap
from fwm.cli.subcmds.compose import compose
from fwm.database.schema import Sysroot, DecoupledTrace, DecoupledCheckpoint
from fwm.repo.errors import FwmValidationError
from fwm.repo.gc import collect_garbage, find_leftovers
from fwm.repo.lookup import get_sysroot, resolve_checkpoint, resolve_trace
from fwm.repo.repo import Repo
from fwm.utils.log import Logger

# The modules that read spike's files, catalog trees or copy repositories load pydantic: each command imports them when
# it runs, so that completing `fwm repo ...` (which imports this module) stays light


@click.group(cls=SectionedGroup, sections=(
    ("The repository", ("init", "verify", "gc")),
    ("Copying between repositories", ("pull", "push", "subrepo")),
    ("Changing its content (needs write access)", ("compose",)),
    ("Making its traces and checkpoints (only reads it)", ("bootstrap",)),
))
def repo():
    """
    Create and change the workload repository: the commands of its creator.

    `fwm repo bootstrap` generates the runs that record its traces and create its checkpoints, and checks their
    outputs; `fwm repo compose` imports them.
    """


repo.add_command(compose)
repo.add_command(bootstrap)


@repo.command(short_help="Create an empty workload repository.")
@click.argument("path", type=click.Path(file_okay=False))
def init(path):
    """
    Create an empty workload repository at PATH.
    """
    Repo.init(path).close()
    click.echo(os.path.abspath(path))


@repo.command("verify", short_help="Check the content, and look for leftovers.")
@click.argument("kind", type=click.Choice(["sysroot", "trace", "checkpoint"]), required=False)
@click.argument("id", required=False, shell_complete=complete_by_kind)
@click.pass_context
def verify_cmd(ctx, kind, id):
    """
    Verify the content of the repository against its catalogs: everything, or one sysroot, trace or checkpoint.

    Verifying everything also reports the leftovers of interrupted commands, which `fwm repo gc` removes. Prints each
    problem found, and fails if there is any.
    """
    from fwm.repo.checkpoint import verify_checkpoint
    from fwm.repo.sysroot import verify_sysroot
    from fwm.repo.trace import verify_trace
    r = open_repo(ctx)
    if kind is not None and id is None:
        raise click.UsageError(f"give the {kind} to verify")

    items = []
    if kind in (None, "sysroot"):
        sysroots = [get_sysroot(id)] if kind else list(Sysroot.select().order_by(Sysroot.name))
        items += [(f"sysroot {s.name}", lambda s=s: verify_sysroot(r, s)) for s in sysroots]
    if kind in (None, "trace"):
        traces = [resolve_trace(id)] if kind else list(DecoupledTrace.select())
        items += [(f"trace {t.uuid}", lambda t=t: verify_trace(r, t)) for t in traces]
    if kind in (None, "checkpoint"):
        ckpts = [resolve_checkpoint(id)] if kind else list(DecoupledCheckpoint.select())
        items += [(f"checkpoint {c.trace.uuid}:{c.name}", lambda c=c: verify_checkpoint(r, c)) for c in ckpts]

    failed = 0
    for label, verify in items:
        problems = verify()
        if problems:
            failed += 1
        for path, reason in problems:
            click.echo(f"{label}: {path}: {reason}")
    leftovers = find_leftovers(r) if kind is None else []
    for leftover in leftovers:
        click.echo(f"leftover {leftover.path}: {leftover.reason}")
    Logger.get_logger().info(
        f"verified {len(items)} item(s), {failed} with problems" +
        (f"; {len(leftovers)} leftover(s), which `fwm repo gc` removes" if leftovers else ""))
    if failed or leftovers:
        problems = ([f"{failed} item(s) failed the verification"] if failed else []) + \
            ([f"{len(leftovers)} leftover(s) found, which `fwm repo gc` removes"] if leftovers else [])
        raise FwmValidationError("; ".join(problems))


@repo.command("gc", short_help="Remove the leftovers of interrupted commands.")
@click.option("--dry-run", "-n", is_flag=True, help="Only print what would be removed.")
@click.pass_context
def gc_cmd(ctx, dry_run):
    """
    Remove the leftovers of interrupted commands: the staging of imports that were killed, and files that no
    sysroot, trace or checkpoint refers to.

    Prints each leftover removed (or, with --dry-run, found).
    """
    r = open_repo(ctx, write=not dry_run)
    leftovers = find_leftovers(r) if dry_run else collect_garbage(r)
    for leftover in leftovers:
        click.echo(f"{leftover.path}: {leftover.reason}")
    Logger.get_logger().info(f"{'found' if dry_run else 'removed'} {len(leftovers)} leftover(s)")


def _transfer_options(f):
    f = click.option("--no-checkpoints", is_flag=True, help="Copy the traces, but none of their checkpoints.")(f)
    f = click.option("--no-traces", is_flag=True,
                     help="Copy only the sysroots and entry points: no trace, and so no checkpoint.")(f)
    return f


def _check_entries(entries, everything):
    if entries and everything:
        raise click.UsageError("give either the entry points to copy or --all, not both")
    if not entries and not everything:
        raise click.UsageError("give the entry points to copy, or --all")


def _transfer(copy, repo, other, entries, everything, no_traces, no_checkpoints):
    result = copy(repo, other, entries, everything=everything, traces=not no_traces, checkpoints=not no_checkpoints,
                  report=lambda item: click.echo(str(item)))
    Logger.get_logger().info(result.summary())


_TRANSFER_HELP = """
    Each entry point brings its workload and its sysroot, and its traces with their checkpoints. Their rows are copied
    as they are: trace UUIDs, provenance, metadata, validation results.

    What the receiving repository already has is kept: the same sysroot, entry point, recording or checkpoint is
    present, and its rows don't change. The same name with something else is a conflict: then nothing is copied, and
    it fails with status 5. Each entry point is committed on its own, so an interrupted copy can be run again to
    complete it.

    Prints each item as `added|present <kind> <id>`, once its entry point is committed.
    """


@repo.command("pull", short_help="Copy entry points of another repository into this one.")
@click.argument("source", type=click.Path(file_okay=False))  # (a missing one isn't a repository: status 2)
@click.argument("entries", nargs=-1, shell_complete=complete_source_entry)
@click.option("--all", "everything", is_flag=True,
              help="Copy every entry point of SOURCE, and every sysroot that has none.")
@_transfer_options
@click.pass_context
def pull_cmd(ctx, source, entries, everything, no_traces, no_checkpoints):
    from fwm.repo.transfer import pull
    _check_entries(entries, everything)
    _transfer(pull, open_repo(ctx, write=True), source, entries, everything, no_traces, no_checkpoints)


pull_cmd.help = """
    Copy the ENTRIES, entry points of the repository SOURCE, into this repository (or with --all, everything SOURCE
    has). SOURCE is only read.
    """ + _TRANSFER_HELP


@repo.command("push", short_help="Copy entry points of this repository into another one.")
@click.argument("dest", type=click.Path(exists=True, file_okay=False))
@click.argument("entries", nargs=-1, shell_complete=complete_other_entry)
@click.option("--all", "everything", is_flag=True,
              help="Copy every entry point of this repository, and every sysroot that has none.")
@_transfer_options
@click.pass_context
def push_cmd(ctx, dest, entries, everything, no_traces, no_checkpoints):
    from fwm.repo.transfer import push
    _check_entries(entries, everything)
    _transfer(push, open_repo(ctx), dest, entries, everything, no_traces, no_checkpoints)


push_cmd.help = """
    Copy the ENTRIES, entry points of this repository, into the repository DEST (or with --all, everything this one
    has). This repository is only read.
    """ + _TRANSFER_HELP


@repo.command("subrepo", short_help="Create a repository with some entry points of this one.")
@click.argument("new_repo", type=click.Path(file_okay=False))
@click.argument("entries", nargs=-1, required=True, shell_complete=complete_other_entry)
@_transfer_options
@click.pass_context
def subrepo_cmd(ctx, new_repo, entries, no_traces, no_checkpoints):
    """
    Create the repository NEW_REPO with the ENTRIES, entry points of this repository, and their sysroots, traces and
    checkpoints: `fwm repo init NEW_REPO`, then `fwm repo push NEW_REPO ENTRIES...`. If the copy fails, NEW_REPO is
    removed (an empty directory it was created in is left empty). This repository is only read.

    NEW_REPO must not exist, or must be an empty directory. Prints its path.
    """
    from fwm.repo.subrepo import create_subrepo
    result = create_subrepo(open_repo(ctx), new_repo, entries, traces=not no_traces, checkpoints=not no_checkpoints)
    Logger.get_logger().info(result.summary())
    click.echo(os.path.abspath(new_repo))
