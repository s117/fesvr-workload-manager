#!/usr/bin/env python3
import click

from fwm.repo.errors import FwmError
from fwm.repo.repo import Repo


class FwmGroup(click.Group):
    """The fwm command group: reports an FwmError as a message, and exits with its exit code."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except FwmError as e:
            click.echo(f"error: {e}", err=True)
            ctx.exit(e.exit_code)


@click.group(cls=FwmGroup)
@click.pass_context
@click.option("--repo-path", envvar='FWM_REPO_PATH', type=click.Path(file_okay=False),
              help='The workload repository. Defaults to the environment variable "FWM_REPO_PATH".')
def cli(ctx, repo_path):
    """
    The RISCV FESVR Workload Manager (fwm).

    \b
    Exit status: 0 success, 1 unexpected error, 2 usage error, 3 not found, 4 failed check.
    """
    ctx.ensure_object(dict)
    ctx.obj["repo_path"] = repo_path


def open_repo(ctx: click.Context, write: bool = False) -> Repo:
    """
    The repository given by --repo-path or FWM_REPO_PATH, opened once and closed with the command.

    :param write: The command changes the repository. Otherwise it's opened read-only, so that it can be used without
                  write permission.
    """
    obj = ctx.find_root().obj
    if "repo" not in obj:
        if not obj.get("repo_path"):
            raise click.UsageError("no workload repository: pass --repo-path or set FWM_REPO_PATH")
        obj["repo"] = Repo.open(obj["repo_path"], readonly=not write)
        ctx.find_root().call_on_close(obj["repo"].close)
    return obj["repo"]


def _register_commands() -> None:
    from fwm.cli.subcmds.completion import completion
    from fwm.cli.subcmds.list import list_cmd
    from fwm.cli.subcmds.mkgen import mkgen
    from fwm.cli.subcmds.repo import repo
    from fwm.cli.subcmds.show import show
    from fwm.cli.subcmds.spawn import spawn

    for command in (repo, list_cmd, show, spawn, mkgen, completion):
        cli.add_command(command)


_register_commands()
