import click

from fwm.cli.completion import complete_entry, complete_trace
from fwm.cli.fwm import open_repo
from fwm.cli.subcmds.mkgen import done, out_dir_option
from fwm.mkgen.makefile import generate_record, generate_create_checkpoints


@click.group()
def bootstrap():
    """
    Generate the runs that make the repository's traces and checkpoints, for its creator. Import their results with
    `fwm repo add trace` and `fwm repo add checkpoint`.

    \b
    Like `fwm mkgen`, they generate a run directory (a Makefile and run.json) to run with `make -C <dir> run`, and
    only read the repository.
    """


@bootstrap.command()
@click.argument("entry", shell_complete=complete_entry)
@out_dir_option
@click.pass_context
def record(ctx, entry, out_dir):
    """
    Record a run of the entry point ENTRY in a fresh copy of its sysroot.

    \b
    Outputs trace/, final-state.json, console.stdout and console.stderr. Import the trace with
      fwm repo add trace ENTRY <dir>/trace --final-state <dir>/final-state.json
    """
    done(generate_record(open_repo(ctx), entry, out_dir))


@bootstrap.command("create-checkpoints")
@click.argument("trace", shell_complete=complete_trace)
@click.argument("desc", type=click.Path(exists=True, dir_okay=False))
@out_dir_option
@click.pass_context
def create_checkpoints(ctx, trace, desc, out_dir):
    """
    Replay TRACE (a trace UUID or prefix, or an entry point with one trace), and create the checkpoints of DESC, a
    description file of "<name>: <instruction count>" lines.

    \b
    Outputs ckpt.desc (a copy of DESC), checkpoints/ and final-state.json. Import the checkpoints with
      fwm repo add checkpoint TRACE <dir>/ckpt.desc <dir>/checkpoints
    """
    done(generate_create_checkpoints(open_repo(ctx), trace, desc, out_dir))
