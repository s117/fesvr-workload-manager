import click

from fwm.cli.completion import complete_sysroot
from fwm.cli.fwm import open_repo
from fwm.repo.sysroot import spawn_sysroot


@click.command()
@click.argument("sysroot_name", shell_complete=complete_sysroot)
@click.argument("dest", type=click.Path())
@click.option("--force", "-f", is_flag=True, help="Replace DEST if it exists.")
@click.pass_context
def spawn(ctx, sysroot_name, dest, force):
    """
    Make a working copy of the sysroot SYSROOT_NAME at DEST, with the permissions of its catalog, and verify it.
    """
    spawn_sysroot(open_repo(ctx), sysroot_name, dest, force)
