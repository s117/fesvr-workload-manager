import logging
import subprocess
import sys

from fwm.utils.log import Logger


def test_messages_plain_in_a_log(tmp_path, sysroot_src):
    """Messages written to a file or a pipe (a job's log) carry no color codes; a terminal gets colors."""
    repo = str(tmp_path / "r")
    subprocess.run([sys.executable, "-m", "fwm", "repo", "init", repo], check=True, capture_output=True)
    result = subprocess.run([sys.executable, "-m", "fwm", "--repo-path", repo, "repo", "compose", "add", "sysroot",
                             str(sysroot_src), "sr"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "absolute symlink" in result.stderr and "\x1b[" not in result.stderr

    record = logging.LogRecord("fwm", logging.WARNING, "f.py", 1, "careful", None, None)
    assert "\x1b[" in Logger.ColorFormatter(color=True).format(record)
