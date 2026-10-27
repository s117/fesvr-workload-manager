import click

from fwm.cli.completion import complete_entry
from fwm.cli.fwm import open_repo
from fwm.repo.spawn import spawn as spawn_entry, SpawnMode


@click.command()
@click.argument("entry", shell_complete=complete_entry)
@click.argument("dest", type=click.Path())
@click.option("--mode", type=click.Choice([m.value for m in SpawnMode]), default=SpawnMode.AUTO.value,
              show_default=True,
              help="copy: copy every file. link: link to the repository's files (fast, but the program can't "
                   "write to them). auto: as the entry point's copy_spawn says.")
@click.option("--force", "-f", is_flag=True, help="Replace DEST if it exists.")
@click.pass_context
def spawn(ctx, entry, dest, mode, force):
    """
    Make a working copy of an entry point's sysroot, to run its program in.

    DEST must not exist, unless --force replaces it. The copy is checked against the sysroot's catalog. Its
    directories are real, with the catalog's permissions. Its files are copies, or links to the repository's read-only
    files (see --mode). The Makefiles of `fwm mkgen from-sysroot` run it for you.

    \b
    Examples:
      fwm spawn bfs-road-n64 sysroot                as its entry point says
      fwm spawn --mode copy bfs-road-n64 sysroot    a full, writable copy
      fwm spawn --force bfs-road-n64 sysroot        replace an earlier one
    """
    how = spawn_entry(open_repo(ctx), entry, dest, force, SpawnMode(mode))
    click.echo(f"spawned the sysroot of '{entry}' at {dest} ({how.value})", err=True)
