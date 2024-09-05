#!/usr/bin/env python3
import click


@click.group()
@click.pass_context
@click.option("--repo-path", envvar='FWM_REPO_PATH',
              type=click.Path(exists=True, dir_okay=True, file_okay=False),
              help='Override the workload repository path given by the environmental variable "FWM_REPO_PATH".')
def cli(ctx, repo_path):
    """
    The RISCV FESVR Workload Manager (fwm).
    """
    ctx.ensure_object(dict)
    # if not repo_path:

    ctx.obj["repo_path"] = repo_path
