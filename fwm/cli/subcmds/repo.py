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
    Create and maintain a repository: the commands of its creator.

    `fwm repo bootstrap` generates the runs that make traces and checkpoints, and checks their outputs. `fwm repo
    compose` imports them, and changes the content. The other commands work on the repository as a whole.

    \b
    Examples:
      # Create a repository, and use it by default
      fwm repo init ~/fwm-repo
      export FWM_REPO_PATH=~/fwm-repo

    \b
      # Check all of it
      fwm repo verify

    \b
      # A new repository with one entry point of this one
      fwm repo subrepo ~/small bfs-road-n64
    """


repo.add_command(compose)
repo.add_command(bootstrap)


@repo.command(short_help="Create an empty workload repository.")
@click.argument("path", metavar="DIR", type=click.Path(file_okay=False))
def init(path):
    """
    Create an empty repository in DIR, a new or empty directory. Prints its path.

    \b
    Examples:
      # Create a repository, and use it by default
      fwm repo init ~/fwm-repo
      export FWM_REPO_PATH=~/fwm-repo
    """
    Repo.init(path).close()
    click.echo(os.path.abspath(path))


@repo.command("verify", short_help="Check the content, and look for leftovers.")
@click.argument("kind", type=click.Choice(["sysroot", "trace", "checkpoint"]), required=False, metavar="[KIND]")
@click.argument("id", required=False, metavar="[NAME]", shell_complete=complete_by_kind)
@click.pass_context
def verify_cmd(ctx, kind, id):
    """
    Check the repository's files against their catalogs: all of them, or one item's.

    KIND is sysroot, trace or checkpoint, and NAME the item's name. Checking all also finds the leftovers of
    interrupted commands, which `fwm repo gc` removes. It prints each problem, and fails with status 4 if there is any.
    It reads every file, so it takes a while on a large repository.

    \b
    Examples:
      # Everything, and the leftovers of interrupted commands
      fwm repo verify

    \b
      # One sysroot
      fwm repo verify sysroot road.sg
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
    Remove the leftovers of interrupted commands.

    Leftovers are the staging of imports that were killed, and files that no sysroot, trace or checkpoint refers to.
    It prints each leftover removed, or with --dry-run, each one found.

    \b
    Examples:
      # See what it would remove, then remove it
      fwm repo gc --dry-run
      fwm repo gc
    """
    r = open_repo(ctx, write=not dry_run)
    leftovers = find_leftovers(r) if dry_run else collect_garbage(r)
    for leftover in leftovers:
        click.echo(f"{leftover.path}: {leftover.reason}")
    Logger.get_logger().info(f"{'found' if dry_run else 'removed'} {len(leftovers)} leftover(s)")


def _transfer_options(f):
    f = click.option("--no-checkpoints", is_flag=True, help="Copy the traces, but no checkpoints.")(f)
    f = click.option("--no-traces", is_flag=True, help="Copy no traces, and so no checkpoints.")(f)
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
    \b
    Each entry point brings its workload, sysroot, traces and checkpoints:
    - what the receiving repository already has is kept, unchanged;
    - the same name with other content is a conflict: nothing is copied, and
      it fails with status 5;
    - an interrupted copy can be run again to complete it.

    It prints each item as "added" or "present", with its kind and name. See the README, "Merging and subsets".
    """


@repo.command("pull", short_help="Copy entry points of another repository into this one.")
@click.argument("source", type=click.Path(file_okay=False))  # (a missing one isn't a repository: status 2)
@click.argument("entries", nargs=-1, metavar="[ENTRY]...", shell_complete=complete_source_entry)
@click.option("--all", "everything", is_flag=True, help="Copy everything SOURCE has.")
@_transfer_options
@click.pass_context
def pull_cmd(ctx, source, entries, everything, no_traces, no_checkpoints):
    from fwm.repo.transfer import pull
    _check_entries(entries, everything)
    _transfer(pull, open_repo(ctx, write=True), source, entries, everything, no_traces, no_checkpoints)


pull_cmd.help = """
    Copy entry points of the repository SOURCE into this one, with what they need. SOURCE is only read.
    """ + _TRANSFER_HELP + """
    \b
    Examples:
      # Two entry points of a shared repository
      fwm repo pull /shared/repo bfs-road-n64 pr-road

    \b
      # Everything it has
      fwm repo pull /shared/repo --all

    \b
      # An entry point and its traces, without checkpoints
      fwm repo pull /shared/repo bfs-road-n64 --no-checkpoints
    """


@repo.command("push", short_help="Copy entry points of this repository into another one.")
@click.argument("dest", type=click.Path(exists=True, file_okay=False))
@click.argument("entries", nargs=-1, metavar="[ENTRY]...", shell_complete=complete_other_entry)
@click.option("--all", "everything", is_flag=True, help="Copy everything this repository has.")
@_transfer_options
@click.pass_context
def push_cmd(ctx, dest, entries, everything, no_traces, no_checkpoints):
    from fwm.repo.transfer import push
    _check_entries(entries, everything)
    _transfer(push, open_repo(ctx), dest, entries, everything, no_traces, no_checkpoints)


push_cmd.help = """
    Copy entry points of this repository into DEST, an existing repository, with what they need. This repository is
    only read.
    """ + _TRANSFER_HELP + """
    \b
    Examples:
      # One entry point, into an existing repository
      fwm repo push ~/merged bfs-road-n64

    \b
      # Everything this one has
      fwm repo push ~/merged --all
    """


@repo.command("subrepo", short_help="Create a repository with some entry points of this one.")
@click.argument("new_repo", type=click.Path(file_okay=False))
@click.argument("entries", nargs=-1, required=True, metavar="ENTRY...", shell_complete=complete_other_entry)
@_transfer_options
@click.pass_context
def subrepo_cmd(ctx, new_repo, entries, no_traces, no_checkpoints):
    """
    Create the repository NEW_REPO with some entry points of this one, and what they need.

    It runs `fwm repo init NEW_REPO`, then `fwm repo push NEW_REPO ENTRY...`. NEW_REPO must not exist, or must be an
    empty directory. If the copy fails, NEW_REPO is removed again. This repository is only read. Prints NEW_REPO's
    path.

    \b
    Examples:
      # A new repository with two entry points
      fwm repo subrepo ~/small bfs-road-n64 pr-road

    \b
      # The same, without checkpoints
      fwm repo subrepo ~/small bfs-road-n64 pr-road --no-checkpoints
    """
    from fwm.repo.subrepo import create_subrepo
    result = create_subrepo(open_repo(ctx), new_repo, entries, traces=not no_traces, checkpoints=not no_checkpoints)
    Logger.get_logger().info(result.summary())
    click.echo(os.path.abspath(new_repo))
