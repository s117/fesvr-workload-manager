import click
from click.shell_completion import get_completion_class

COMPLETE_VAR = "_FWM_COMPLETE"


@click.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@click.pass_context
def completion(ctx, shell):
    """
    Print the shell completion script.

    Completion offers fwm's commands and options, and the names in the repository of --repo-path or FWM_REPO_PATH.
    fwm must be on PATH.

    \b
    Examples, for your shell's startup file:
      eval "$(fwm completion zsh)"          in ~/.zshrc
      eval "$(fwm completion bash)"         in ~/.bashrc
      fwm completion fish | source          in ~/.config/fish/config.fish
    """
    completion_class = get_completion_class(shell)
    assert completion_class is not None
    click.echo(completion_class(ctx.find_root().command, {}, "fwm", COMPLETE_VAR).source(), nl=False)
