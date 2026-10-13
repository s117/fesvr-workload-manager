class FwmError(Exception):
    """An error fwm reports to the user. exit_code is the exit status of the fwm command that failed with it."""
    exit_code = 1


class NotFoundError(FwmError):
    """The named sysroot, entry point, trace or checkpoint doesn't exist."""
    exit_code = 3


class FwmValidationError(FwmError):
    """The input or the repository content fails a check."""
    exit_code = 4
