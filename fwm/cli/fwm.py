#!/usr/bin/env python3
import importlib
import signal
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import click

from fwm.repo.errors import FwmError
from fwm.repo.repo import Repo


def _exit_on_signal(signum, frame):
    """End like Ctrl-C does: by an exception, so the cleanup of an interrupted import or removal runs."""
    raise SystemExit(128 + signum)


# The module, attribute and short help of each subcommand. A subcommand is imported only when it runs, since loading
# every module from a shared filesystem takes seconds. Its short help is kept here, so that `fwm --help` and completing
# a subcommand name import none of them
_SUBCOMMANDS = {
    "repo": ("fwm.cli.subcmds.repo", "repo", "Create and change the workload repository."),
    "list": ("fwm.cli.subcmds.list", "list_cmd", "List workloads, sysroots, entry points, traces or checkpoints."),
    "show": ("fwm.cli.subcmds.show", "show", "Show a workload, sysroot, entry point, trace or checkpoint."),
    "spawn": ("fwm.cli.subcmds.spawn", "spawn", "Make a working copy of an entry point's sysroot."),
    "mkgen": ("fwm.cli.subcmds.mkgen", "mkgen", "Generate a run directory that begins a simulation."),
    "completion": ("fwm.cli.subcmds.completion", "completion", "Print the script that enables shell completion."),
}


# The sections of `fwm --help`: the daily commands of a repository's users first
_SECTIONS = (
    ("Daily", ("list", "show", "mkgen", "spawn")),
    ("Setup and maintenance", ("completion", "repo")),
)

# Not offered when completing a subcommand name: once completion works, its script is already installed
_NOT_COMPLETED = ("completion",)


def format_sections(formatter: click.HelpFormatter, sections: Iterable[Tuple[str, Sequence[str]]],
                    short_help: Dict[str, str]) -> None:
    """Write the commands of short_help (name: short help) under titled sections; the others last, under "Commands"."""
    in_sections = {name for _, names in sections for name in names}
    rest = sorted(name for name in short_help if name not in in_sections)
    width = max(len(name) for name in short_help)  # one column of names across the sections
    for title, names in [*sections, ("Commands", rest)]:
        rows = [(name.ljust(width), short_help[name]) for name in names if name in short_help]
        if rows:
            with formatter.section(title):
                formatter.write_dl(rows)


class SectionedGroup(click.Group):
    """A command group whose help lists its commands under titled sections: sections=[(title, [name, ...]), ...]."""

    def __init__(self, *args: Any, sections: Sequence[Tuple[str, Sequence[str]]] = (), **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.sections = sections

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        commands = {name: self.get_command(ctx, name) for name in self.list_commands(ctx)}
        visible = {name: command for name, command in commands.items() if command is not None and not command.hidden}
        if not visible:
            return
        limit = formatter.width - 6 - max(len(name) for name in visible)
        format_sections(formatter, self.sections,
                        {name: command.get_short_help_str(limit) for name, command in visible.items()})


class FwmGroup(click.Group):
    """
    The fwm command group: imports a subcommand when it runs, reports an FwmError as a message, and exits with its
    exit code.
    """

    def list_commands(self, ctx: click.Context) -> List[str]:
        return sorted(_SUBCOMMANDS)

    def get_command(self, ctx: click.Context, cmd_name: str):
        if cmd_name not in self.commands and cmd_name in _SUBCOMMANDS:
            module, attr, short_help = _SUBCOMMANDS[cmd_name]
            command = getattr(importlib.import_module(module), attr)
            command.short_help = short_help
            self.add_command(command, cmd_name)
        return super().get_command(ctx, cmd_name)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        format_sections(formatter, _SECTIONS, {name: _SUBCOMMANDS[name][2] for name in self.list_commands(ctx)})

    def shell_complete(self, ctx: click.Context, incomplete: str):
        from click.shell_completion import CompletionItem

        names = [CompletionItem(name, help=_SUBCOMMANDS[name][2])
                 for name in self.list_commands(ctx) if name.startswith(incomplete) and name not in _NOT_COMPLETED]
        # the options: click.Group's own version would also import every subcommand for its help
        return names + click.Command.shell_complete(self, ctx, incomplete)

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

