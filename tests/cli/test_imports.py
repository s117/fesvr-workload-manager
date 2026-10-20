"""
The read-only commands load only what they need: on a shared filesystem, every module costs file lookups, and loading
pydantic and the spike file readers made `fwm list` take seconds on a busy login node.
"""
import importlib
import json
import subprocess
import sys
import textwrap

import click
import pytest

from fwm.cli.fwm import _SUBCOMMANDS

# Modules the fast commands must not load
HEAVY = ("pydantic", "colorama", "importlib.metadata")


def loaded_modules(repo_path, args, complete=False):
    """The modules a fresh Python process has loaded after running fwm with args (or completing them)."""
    script = textwrap.dedent(f"""
        import json, sys
        from fwm.cli.fwm import cli
        args = ["--repo-path", {repo_path!r}] + {list(args)!r}
        if {complete!r}:
            from click.shell_completion import ShellComplete
            ShellComplete(cli, {{}}, "fwm", "_FWM_COMPLETE").get_completions(args, "")
        else:
            cli.main(args, prog_name="fwm", standalone_mode=False)
        print(json.dumps(sorted(sys.modules)), file=sys.stderr)
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    return set(json.loads(result.stderr.strip().splitlines()[-1]))


@pytest.mark.parametrize("args", [
    ["list"],
    ["list", "traces", "--json"],
    ["list", "checkpoints"],
    ["show", "sysroot", "sr"],
    ["show", "entry", "e"],
    ["show", "trace", "e"],
    ["show", "--json", "checkpoint", "e.2.0.40"],
])
def test_read_only_commands_load_no_heavy_module(populated, tmp_path, args):
    assert not loaded_modules(str(tmp_path / "repo"), args) & set(HEAVY)


@pytest.mark.parametrize("args", [
    [], ["show", "trace"], ["mkgen", "from-checkpoint"],
    ["repo", "set-metadata"], ["repo", "remove", "entry"], ["repo", "add", "checkpoint"],
    ["repo", "pull", "{repo}"], ["repo", "push", "{repo}"], ["repo", "subrepo", "new"],
])
def test_completion_loads_no_heavy_module(populated, tmp_path, args):
    repo_path = str(tmp_path / "repo")
    args = [arg.format(repo=repo_path) for arg in args]
    assert not loaded_modules(repo_path, args, complete=True) & set(HEAVY)


@pytest.mark.parametrize("complete", [False, True])
def test_help_and_subcommand_names_import_no_subcommand(tmp_path, complete):
    modules = loaded_modules(str(tmp_path / "repo"), [] if complete else ["--help"], complete=complete)
    assert not [m for m in modules if m.startswith("fwm.cli.subcmds.")]


@pytest.mark.parametrize("name", sorted(_SUBCOMMANDS))
def test_subcommand_table(name):
    module, attr, short_help = _SUBCOMMANDS[name]
    command = getattr(importlib.import_module(module), attr)
    assert isinstance(command, click.Command) and command.name == name
    assert short_help.endswith(".") and len(short_help) <= 62  # one line in `fwm --help` at 80 columns


def test_mkgen_loads_no_pydantic(populated, tmp_path):
    # mkgen writes fwm's version (importlib.metadata) and logs (colorama), but reads no spike file
    modules = loaded_modules(str(tmp_path / "repo"), ["mkgen", "from-fesvr-trace", "e", "-o", str(tmp_path / "run")])
    assert "pydantic" not in modules
