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
