PACKAGE = "fesvr-workload-manager"


def fwm_version() -> str:
    """The version of the installed fwm package, or "unknown" when fwm runs from a source tree without metadata."""
    # imported here: it loads many modules, and the version is only needed when something is written
    from importlib import metadata

    try:
        return metadata.version(PACKAGE)
    except metadata.PackageNotFoundError:
        return "unknown"


def fwm_build() -> str:
    """
    Which fwm this is, for `fwm --version`: the package version, and where it was installed from when the installer
    recorded it (PEP 610): the git commit of an install from git, or the source tree of an editable install, with that
    tree's commit. It never fails: what can't be found is left out.
    """
    import json
    import subprocess
    import urllib.parse
    from importlib import metadata

    version = fwm_version()
    try:
        origin = json.loads(metadata.distribution(PACKAGE).read_text("direct_url.json") or "null")
        url = origin["url"]
    except (metadata.PackageNotFoundError, ValueError, TypeError, KeyError):
        return version
    commit = (origin.get("vcs_info") or {}).get("commit_id")
    if commit:
        return f"{version} (commit {commit[:12]}, from {url})"
    parsed = urllib.parse.urlparse(url)
    if (origin.get("dir_info") or {}).get("editable") and parsed.scheme == "file":
        tree = urllib.parse.unquote(parsed.path)
        try:
            # safe.directory: the tree may be another user's checkout, which git refuses to read otherwise
            described = subprocess.run(["git", "-c", "safe.directory=*", "-C", tree, "describe", "--always", "--dirty",
                                        "--abbrev=12"], capture_output=True, text=True)
            where = f"commit {described.stdout.strip()}, " if described.returncode == 0 else ""
        except OSError:  # no git
            where = ""
        return f"{version} ({where}editable install of {tree})"
    return f"{version} (from {url})"
