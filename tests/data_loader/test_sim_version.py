import pytest

from fwm.data_loader.sim_version import parse_sim_version, read_sim_version, SimVersionError

# The output of `spike --version` (riscv-isa-sim 0d7a85c)
SPIKE_VERSION = """\
spike build information:
  Build hostname  - builder
  Build host      - Linux 6.18.0 x86_64
  Build target    - Linux-6.18.0
  C++ compiler    - GNU 16.2.1 20260810 (/usr/bin/c++)
  Build date      - 2026-10-14T17:48:26-0400
  Build type      - RelO3 -O3 -g
  Repository      - git@github.com:s117/riscv-isa-sim.git
  Version         - heads/anycore-gcc_16.1.0-llvm_22.1.8-0-g0d7a85c
  Commit          - 0d7a85c80db593a638d33cb655d866b174d69bde
FESVR (libfesvr) build information:
  Build hostname  - builder
  Build host      - Linux 6.18.0 x86_64
  Build target    - Linux-6.18.0
  C++ compiler    - GNU 16.2.1 20260810 (/usr/bin/c++)
  Build date      - 2026-10-14T17:48:26-0400
  Build type      - RelO3 -O3 -g
  Repository      - https://github.com/s117/riscv-fesvr.git
  Version         - heads/anycore-gcc_16.1.0-llvm_22.1.8-0-g4fce14b
  Commit          - 4fce14b1b852a43ca5150667e850611c7a4050da
"""


def test_parse():
    builds = parse_sim_version(SPIKE_VERSION)
    assert list(builds) == ["spike", "FESVR (libfesvr)"]
    assert builds["spike"]["Commit"] == "0d7a85c80db593a638d33cb655d866b174d69bde"
    assert builds["spike"]["C++ compiler"] == "GNU 16.2.1 20260810 (/usr/bin/c++)"
    assert builds["FESVR (libfesvr)"]["Version"] == "heads/anycore-gcc_16.1.0-llvm_22.1.8-0-g4fce14b"
    assert builds["FESVR (libfesvr)"]["Repository"] == "https://github.com/s117/riscv-fesvr.git"


def test_unknown_git_fields():
    # outside a git checkout, the git fields are "unknown"
    text = "spike build information:\n  Version         - unknown\n  Commit          - unknown\n"
    assert parse_sim_version(text) == {"spike": {"Version": "unknown", "Commit": "unknown"}}


@pytest.mark.parametrize("text, match", [
    ("", "no build information"),
    ("usage: spike [host options] <target program>\n", "line 1 is not build information"),
    ("  Version         - x\n", "line 1 is not build information"),
    ("spike build information:\n  Version         - x\n", "spike has no Commit"),
])
def test_not_version_output(text, match):
    with pytest.raises(SimVersionError, match=match):
        parse_sim_version(text)


def test_read(tmp_path):
    path = tmp_path / "sim-version.txt"
    path.write_text(SPIKE_VERSION)
    assert read_sim_version(str(path)) == SPIKE_VERSION
    path.write_text("spike: unknown option --version\n")
    with pytest.raises(SimVersionError):
        read_sim_version(str(path))
