PACKAGE = "fesvr-workload-manager"


def fwm_version() -> str:
    """The version of the installed fwm package, or "unknown" when fwm runs from a source tree without metadata."""
    # imported here: it loads many modules, and the version is only needed when something is written
    from importlib import metadata

    try:
        return metadata.version(PACKAGE)
    except metadata.PackageNotFoundError:
        return "unknown"
