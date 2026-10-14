from importlib import metadata

PACKAGE = "fesvr-workload-manager"


def fwm_version() -> str:
    """The version of the installed fwm package, or "unknown" when fwm runs from a source tree without metadata."""
    try:
        return metadata.version(PACKAGE)
    except metadata.PackageNotFoundError:
        return "unknown"
