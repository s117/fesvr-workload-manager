"""
The read-only commands load only what they need: on a shared filesystem, every module costs file lookups, and loading
pydantic and the spike file readers made `fwm list` take seconds on a busy login node.
"""
import json
import subprocess
import sys
import textwrap

import pytest

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


# Not the subcommand names: their completions show each one's help, which imports them all
@pytest.mark.parametrize("args", [["show", "trace"], ["mkgen", "from-checkpoint"]])
def test_completion_loads_no_heavy_module(populated, tmp_path, args):
    assert not loaded_modules(str(tmp_path / "repo"), args, complete=True) & set(HEAVY)


def test_mkgen_loads_no_pydantic(populated, tmp_path):
    # mkgen writes fwm's version (importlib.metadata) and logs (colorama), but reads no spike file
    modules = loaded_modules(str(tmp_path / "repo"), ["mkgen", "from-fesvr-trace", "e", "-o", str(tmp_path / "run")])
    assert "pydantic" not in modules
