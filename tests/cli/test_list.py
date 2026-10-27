"""fwm list: the names of a kind, filtered by the entities they are related to."""
import json
import shutil
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from fwm.cli.fwm import cli
from fwm.repo.entry import add_entry
from fwm.repo.repo import Repo
from fwm.repo.sysroot import add_sysroot
from tests.conftest import import_checkpoints
from tests.repo.test_transfer import make_checkpoint, record


@pytest.fixture
def repo(tmp_path, sysroot_src, write_entry_point):
    """
    \b
    workloads   w1, w2
    sysroots    s1, s2, and s3 without entry points
    entries     a, b (w1 in s1), c (w2 in s2), d (w2 in s1, no trace)
    traces      ta (a), tb (b), tc (c)
    checkpoints x.1, x.2 of ta; x.1, y.1 of tb (x.1 twice)
    """
    r = Repo.init(str(tmp_path / "repo"))
    other_src = tmp_path / "src" / "s2"
    shutil.copytree(sysroot_src, other_src, symlinks=True)
    (other_src / "app" / "extra").write_text("extra")
    add_sysroot(r, str(sysroot_src), "s1")
    add_sysroot(r, str(other_src), "s2")
    add_sysroot(r, str(other_src), "s3")
    add_entry(r, "s1", "w1", "a", write_entry_point())
    add_entry(r, "s1", "w1", "b", write_entry_point(command="prog.rv64 b"))
    add_entry(r, "s2", "w2", "c", write_entry_point())
    add_entry(r, "s1", "w2", "d", write_entry_point(command="prog.rv64 d"))
    ta, tb, tc = (str(record(r, tmp_path, e, n)) for n, e in enumerate("abc", 1))
    import_checkpoints(r, ta, [make_checkpoint(tmp_path, 1, "x.1", 1000), make_checkpoint(tmp_path, 1, "x.2", 2000)])
    import_checkpoints(r, tb, [make_checkpoint(tmp_path, 2, "x.1", 1000), make_checkpoint(tmp_path, 2, "y.1", 2000)])
    r.close()
    return SimpleNamespace(path=r.path, ta=ta, tb=tb, tc=tc)


def fwm_list(repo, *args):
    """`fwm list` on the repository: the names it prints; the same as its --json, which it checks."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--repo-path", repo.path, "list", *args])
    assert result.exit_code == 0, result.output
    names = result.stdout.splitlines()
    as_json = runner.invoke(cli, ["--repo-path", repo.path, "list", *args, "--json"])
    assert json.loads(as_json.stdout) == names
    return names


def test_kinds(repo):
    assert fwm_list(repo) == ["a", "b", "c", "d"]  # entries by default
    assert fwm_list(repo, "entries") == ["a", "b", "c", "d"]
    assert fwm_list(repo, "workloads") == ["w1", "w2"]
    assert fwm_list(repo, "sysroots") == ["s1", "s2", "s3"]
    assert sorted(fwm_list(repo, "traces")) == sorted([repo.ta, repo.tb, repo.tc])
    # a name two checkpoints share is given with its trace, as `fwm show checkpoint` takes it
    assert sorted(fwm_list(repo, "checkpoints")) == sorted([f"{repo.ta}:x.1", f"{repo.tb}:x.1", "x.2", "y.1"])


@pytest.mark.parametrize("args, expected", [
    # related through others
    (["checkpoints", "--workload", "w1"], ["{ta}:x.1", "{tb}:x.1", "x.2", "y.1"]),
    (["checkpoints", "--workload", "w2"], []),
    (["entries", "--sysroot", "s1"], ["a", "b", "d"]),
    (["workloads", "--sysroot", "s1"], ["w1", "w2"]),
    (["sysroots", "--workload", "w2"], ["s1", "s2"]),
    (["entries", "--checkpoint", "x.1"], ["a", "b"]),
    (["traces", "--checkpoint", "y*"], ["{tb}"]),
    (["sysroots", "--checkpoint", "x.2"], ["s1"]),
    (["workloads", "--trace", "{tc8}*"], ["w2"]),
    (["checkpoints", "--trace", "{ta8}*"], ["{ta}:x.1", "x.2"]),
    (["entries", "--entry", "d"], ["d"]),
    (["traces", "--entry", "d"], []),
    # a checkpoint as <trace glob>:<name glob>
    (["checkpoints", "--checkpoint", "{tb}:*"], ["{tb}:x.1", "y.1"]),
    (["checkpoints", "--checkpoint", "{tb4}*:x.?"], ["{tb}:x.1"]),
    # globs, and a filter of the listed kind
    (["entries", "--entry", "[ab]"], ["a", "b"]),
    (["entries", "--entry", "?"], ["a", "b", "c", "d"]),
    (["checkpoints", "--checkpoint", "x.*"], ["{ta}:x.1", "{tb}:x.1", "x.2"]),
    # a filter given twice: either; different filters: both
    (["entries", "--sysroot", "s2", "--sysroot", "s3"], ["c"]),
    (["entries", "--sysroot", "s1", "--workload", "w2"], ["d"]),
    (["checkpoints", "--entry", "a", "--checkpoint", "x.1"], ["{ta}:x.1"]),
    # a sysroot without entry points is related to nothing else
    (["sysroots", "--sysroot", "s*"], ["s1", "s2", "s3"]),
    (["sysroots", "--workload", "*"], ["s1", "s2"]),
])
def test_filters(repo, args, expected):
    ids = {"ta": repo.ta, "tb": repo.tb, "tc": repo.tc, "ta8": repo.ta[:8], "tb4": repo.tb[:4], "tc8": repo.tc[:8]}
    args = [arg.format(**ids) for arg in args]
    assert sorted(fwm_list(repo, *args)) == sorted(e.format(**ids) for e in expected)


def test_names_are_what_show_takes(repo):
    runner = CliRunner()
    for kind, show_kind in (("workloads", "workload"), ("sysroots", "sysroot"), ("entries", "entry"),
                            ("traces", "trace"), ("checkpoints", "checkpoint")):
        for name in fwm_list(repo, kind):
            assert runner.invoke(cli, ["--repo-path", repo.path, "show", show_kind, name]).exit_code == 0, name


def test_natural_order(repo, write_entry_point):
    r = Repo.open(repo.path)
    for name in ("e10", "e9", "e2"):
        add_entry(r, "s2", "w2", name, write_entry_point(command=f"prog.rv64 {name}"))
    r.close()
    assert fwm_list(repo, "entries", "--entry", "e*") == ["e2", "e9", "e10"]


def test_unmatched_filter(repo, caplog):
    """A filter that matches no entity of its kind is likely a typo: a warning says so, but it is no error."""
    assert fwm_list(repo, "workloads", "--sysroot", "nope", "--entry", "a") == []
    assert "no sysroot matches 'nope'" in caplog.text
    assert "no entry matches" not in caplog.text


def test_no_positional_filter(repo):
    result = CliRunner().invoke(cli, ["--repo-path", repo.path, "list", "entries", "a"])
    assert result.exit_code == 2
