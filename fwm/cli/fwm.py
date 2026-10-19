#!/usr/bin/env python3
import importlib
import signal

import click

from fwm.repo.errors import FwmError
from fwm.repo.repo import Repo


def _exit_on_signal(signum, frame):
    """End like Ctrl-C does: by an exception, so the cleanup of an interrupted import or removal runs."""
    raise SystemExit(128 + signum)


# The module and attribute of each subcommand. A subcommand is imported only when it runs, since loading every module
# from a shared filesystem takes seconds
_SUBCOMMANDS = {
    "repo": ("fwm.cli.subcmds.repo", "repo"),
    "list": ("fwm.cli.subcmds.list", "list_cmd"),
    "show": ("fwm.cli.subcmds.show", "show"),
    "spawn": ("fwm.cli.subcmds.spawn", "spawn"),
    "mkgen": ("fwm.cli.subcmds.mkgen", "mkgen"),
    "check": ("fwm.cli.subcmds.check", "check"),
    "completion": ("fwm.cli.subcmds.completion", "completion"),
}


class FwmGroup(click.Group):
    """
    The fwm command group: imports a subcommand when it runs, reports an FwmError as a message, and exits with its
    exit code.
    """

    def list_commands(self, ctx: click.Context):
        return sorted(set(self.commands) | set(_SUBCOMMANDS))

    def get_command(self, ctx: click.Context, cmd_name: str):
        if cmd_name not in self.commands and cmd_name in _SUBCOMMANDS:
            module, attr = _SUBCOMMANDS[cmd_name]
            self.add_command(getattr(importlib.import_module(module), attr), cmd_name)
        return super().get_command(ctx, cmd_name)

    def main(self, *args, **kwargs):
        # Batch schedulers stop a job with SIGTERM (or SIGHUP), whose default action skips all cleanup
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, _exit_on_signal)
        return super().main(*args, **kwargs)

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
    Exit status: 0 success, 1 unexpected error, 2 usage error or unusable repository, 3 not found, 4 failed
    check, 5 already exists.
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

