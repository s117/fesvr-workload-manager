import pytest

from fwm.utils.fs import set_tree_writable


@pytest.fixture(autouse=True)
def _writable_tmp_path(request):
    """Tests leave read-only trees (like a repository's), which pytest couldn't remove: make them writable again."""
    yield
    tmp_path = getattr(request.node, "funcargs", {}).get("tmp_path")
    if tmp_path is not None and tmp_path.exists():
        set_tree_writable(str(tmp_path))
