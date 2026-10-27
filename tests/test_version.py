import json
from importlib import metadata

import pytest
from click.testing import CliRunner

from fwm import version
from fwm.cli.fwm import cli


def test_version_option():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.startswith(f"fwm {version.fwm_version()}")


class _Distribution:
    def __init__(self, direct_url):
        self.direct_url = direct_url

    def read_text(self, name):
        assert name == "direct_url.json"
        return json.dumps(self.direct_url) if self.direct_url is not None else None


@pytest.mark.parametrize("direct_url, expected", [
    ({"url": "ssh://git@github.com/x/fwm.git", "vcs_info": {"vcs": "git", "commit_id": "162c105d9e79bfc75de7cef1"}},
     "(commit 162c105d9e79, from ssh://git@github.com/x/fwm.git)"),
    ({"url": "https://example.org/fwm-0.0.1.tar.gz", "archive_info": {}}, "(from https://example.org/fwm-0.0.1.tar.gz)"),
    (None, ""),
])
def test_fwm_build(monkeypatch, direct_url, expected):
    """Where fwm was installed from, as the installer recorded it (PEP 610)."""
    monkeypatch.setattr(metadata, "distribution", lambda name: _Distribution(direct_url))
    monkeypatch.setattr(version, "fwm_version", lambda: "0.0.1")
    assert version.fwm_build() == f"0.0.1 {expected}".strip()


def test_fwm_build_editable(monkeypatch, tmp_path):
    """An editable install names its source tree, with the tree's commit when it is a git checkout."""
    monkeypatch.setattr(metadata, "distribution",
                        lambda name: _Distribution({"url": f"file://{tmp_path}", "dir_info": {"editable": True}}))
    monkeypatch.setattr(version, "fwm_version", lambda: "0.0.1")
    assert version.fwm_build() == f"0.0.1 (editable install of {tmp_path})"  # not a git checkout


def test_fwm_build_never_fails(monkeypatch, tmp_path):
    """A damaged record, a percent-encoded path, or no git: fwm_build leaves out what it can't find."""
    import subprocess
    monkeypatch.setattr(version, "fwm_version", lambda: "0.0.1")

    class Damaged:
        def read_text(self, name):
            return "{not json"

    monkeypatch.setattr(metadata, "distribution", lambda name: Damaged())
    assert version.fwm_build() == "0.0.1"

    tree = tmp_path / "a b"
    tree.mkdir()
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    subprocess.run(["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "x"], check=True)
    url = "file://" + str(tree).replace(" ", "%20")
    monkeypatch.setattr(metadata, "distribution",
                        lambda name: _Distribution({"url": url, "dir_info": {"editable": True}}))
    assert version.fwm_build().startswith("0.0.1 (commit ")
    assert version.fwm_build().endswith(f", editable install of {tree})")

    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))  # no git
    assert version.fwm_build() == f"0.0.1 (editable install of {tree})"
