import click

from fwm.cli.completion import complete_entry
from fwm.cli.fwm import open_repo
from fwm.repo.spawn import spawn as spawn_entry, SpawnMode


@click.command()
@click.argument("entry", shell_complete=complete_entry)
@click.argument("dest", type=click.Path())
@click.option("--mode", type=click.Choice([m.value for m in SpawnMode]), default=SpawnMode.AUTO.value,
              show_default=True,
              help="copy: copy every file. link: link each file to the repository's read-only file (fast, but the "
                   "program can't write to them). auto: as the entry point's copy_spawn says.")
@click.option("--force", "-f", is_flag=True, help="Replace DEST if it exists.")
@click.pass_context
def spawn(ctx, entry, dest, mode, force):
    """
    Make a working copy of the sysroot of the entry point ENTRY at DEST, in which its program can run, and check it
    against the sysroot's catalog.

    The record and from-sysroot Makefiles run it. Directories are always real, with the permissions of the catalog;
    files are copies, or links to the repository's files.
    """
    how = spawn_entry(open_repo(ctx), entry, dest, force, SpawnMode(mode))
    click.echo(f"spawned the sysroot of '{entry}' at {dest} ({how.value})", err=True)
