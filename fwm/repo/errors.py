class FwmError(Exception):
    """An error fwm reports to the user. exit_code is the exit status of the fwm command that failed with it."""
    exit_code = 1


class RepoUnusableError(FwmError):
    """The repository path can't be used: it isn't a repository, its schema is another one, or it is read-only for a
    command that changes it. Like a usage error, the fix is in how fwm is called."""
    exit_code = 2


class NotFoundError(FwmError):
    """The named sysroot, entry point, trace or checkpoint doesn't exist."""
    exit_code = 3


class FwmValidationError(FwmError):
    """The input or the repository content fails a check."""
    exit_code = 4


class AlreadyExistsError(FwmError):
    """What is being added already exists (see --exist-ok)."""
    exit_code = 5
