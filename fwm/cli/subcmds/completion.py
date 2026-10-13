import click
from click.shell_completion import get_completion_class

COMPLETE_VAR = "_FWM_COMPLETE"


@click.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@click.pass_context
def completion(ctx, shell):
    """
    Print the script that enables the shell completion of fwm, including the names in the repository.

    \b
    Enable it in your shell's startup file:
      zsh  (~/.zshrc):                    eval "$(fwm completion zsh)"
      bash (~/.bashrc):                   eval "$(fwm completion bash)"
      fish (~/.config/fish/config.fish):  fwm completion fish | source
    fwm must be on PATH, and the names are completed from --repo-path or FWM_REPO_PATH.
    """
    completion_class = get_completion_class(shell)
    assert completion_class is not None
    click.echo(completion_class(ctx.find_root().command, {}, "fwm", COMPLETE_VAR).source(), nl=False)
