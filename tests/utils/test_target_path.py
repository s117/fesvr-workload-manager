import pytest

from fwm.utils.target_path import normalize_target_path, target_path_to_sysroot_relative


@pytest.mark.parametrize("pwd, path, expect", [
    # absolute paths ignore pwd
    ("/app", "/boot/pk", "/boot/pk"),
    ("/app", "/", "/"),
    # relative paths resolve against pwd
    ("/app", "input.txt", "/app/input.txt"),
    ("/app", "./input.txt", "/app/input.txt"),
    ("/app", "../boot/pk", "/boot/pk"),
    ("/", "app", "/app"),
    # an empty path is the pwd
    ("/app", "", "/app"),
    # empty and "." components are dropped
    ("/app", "/boot//./pk/", "/boot/pk"),
    # ".." at the root stays at the root
    ("/", "../../etc/passwd", "/etc/passwd"),
    ("/app", "/../..", "/"),
    # a pwd that is not normalized, or not absolute, still gives a correct result
    ("app/./data/..", "x", "/app/x"),
    (".", "app", "/app"),
])
def test_normalize_target_path(pwd, path, expect):
    assert normalize_target_path(pwd, path) == expect


@pytest.mark.parametrize("target_path, expect", [
    ("/", "."),
    ("/app", "app"),
    ("/app/input.txt", "app/input.txt"),
])
def test_target_path_to_sysroot_relative(target_path, expect):
    assert target_path_to_sysroot_relative(target_path) == expect
