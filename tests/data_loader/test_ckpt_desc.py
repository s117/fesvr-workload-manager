import pytest

from fwm.data_loader.ckpt_desc import parse_ckpt_desc, CkptDescError


def test_parse():
    text = "\n  astar.5G: 5000000000  \n\nastar.1G:1000000000\r\n\t\nzero : 0\n"
    assert parse_ckpt_desc(text) == [("zero", 0), ("astar.1G", 1000000000), ("astar.5G", 5000000000)]


def test_split_at_last_colon():
    # the name can't hold a ':' anyway, but the split is at the last one, as in FESVR
    with pytest.raises(CkptDescError, match="invalid checkpoint name 'a:b'"):
        parse_ckpt_desc("a:b: 10")


@pytest.mark.parametrize("text, match", [
    ("", "empty"),
    ("  \n\n", "empty"),
    ("name 10", "has no ':'"),
    (": 10", "empty checkpoint name"),
    ("a: -1", "invalid instruction count"),
    ("a: +1", "invalid instruction count"),
    ("a: 1x", "invalid instruction count"),
    ("a: 0x10", "invalid instruction count"),
    ("a: 1 2", "invalid instruction count"),
    ("a:", "invalid instruction count"),
    (f"a: {1 << 64}", "invalid instruction count"),
    ("a: 1\na: 2", "two checkpoints are named 'a'"),
    ("a: 1\nb: 1", "at instruction count 1"),
    ("a/b: 1", "invalid checkpoint name"),
])
def test_invalid(text, match):
    with pytest.raises(CkptDescError, match=match):
        parse_ckpt_desc(text)


def test_max_count():
    assert parse_ckpt_desc(f"a: {(1 << 64) - 1}") == [("a", (1 << 64) - 1)]


@pytest.mark.parametrize("name", [".", ".."])
def test_dot_names(name):
    # FESVR accepts them, then fails to write a checkpoint file of that name
    with pytest.raises(CkptDescError, match="invalid checkpoint name"):
        parse_ckpt_desc(f"{name}: 100\n")


def test_read_like_spike(tmp_path):
    from fwm.data_loader.ckpt_desc import read_ckpt_desc

    path = tmp_path / "d"
    path.write_bytes(b"a: 100\r\nb: 200\n")
    assert read_ckpt_desc(str(path)) == [("a", 100), ("b", 200)]
    # only "\n" ends a line, as in FESVR: a file with "\r" endings is one line, which spike rejects too
    path.write_bytes(b"a: 100\rb: 200\r")
    with pytest.raises(CkptDescError):
        read_ckpt_desc(str(path))
    # not UTF-8: read as bytes, rejected for its name, not by a decoding error
    path.write_bytes(b"\xff: 100\n")
    with pytest.raises(CkptDescError, match="invalid checkpoint name"):
        read_ckpt_desc(str(path))
