import pathlib
import shutil

import pytest


def test_sha256_file(fs):
    from fwm.utils.digest import Digest
    path = pathlib.Path("/test_sha256")
    testfile = (path / "testfile.txt")
    with pytest.raises(FileNotFoundError):
        Digest.digest_file("sha256", str(testfile), False)

    path.mkdir()
    testfile.touch()
    assert Digest.digest_file("sha256", str(testfile), use_cache=False) == \
           "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    with open(str(testfile), "wb") as f:
        f.write(b"sample")
    assert Digest.digest_file("sha256", str(testfile), use_cache=True) == \
           "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert Digest.digest_file("sha256", str(testfile), use_cache=False) == \
           "af2bdbe1aa9b6ec1e2ade1d694f41fc71a831d0268e9891562113d8a62add1bf"
    assert Digest.digest_file("sha256", str(testfile), use_cache=True) == \
           "af2bdbe1aa9b6ec1e2ade1d694f41fc71a831d0268e9891562113d8a62add1bf"

    testfile.unlink()
    assert Digest.digest_file("sha256", str(testfile), use_cache=True) == \
           "af2bdbe1aa9b6ec1e2ade1d694f41fc71a831d0268e9891562113d8a62add1bf"
    with pytest.raises(FileNotFoundError):
        Digest.digest_file("sha256", str(testfile), False)
    assert Digest.digest_file("sha256", str(testfile), use_cache=True) == \
           "af2bdbe1aa9b6ec1e2ade1d694f41fc71a831d0268e9891562113d8a62add1bf"

    shutil.rmtree(str(path))


def test_sha256_buffer():
    from fwm.utils.digest import Digest
    assert Digest.digest_data("sha256", b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert Digest.digest_data("sha256", b"sample") == "af2bdbe1aa9b6ec1e2ade1d694f41fc71a831d0268e9891562113d8a62add1bf"


def test_is_valid_sha256():
    from fwm.utils.digest import Digest
    assert not Digest.is_valid_hexdigest("sha256", "")
    assert not Digest.is_valid_hexdigest("sha256", "1")
    assert Digest.is_valid_hexdigest("sha256", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    assert not Digest.is_valid_hexdigest("sha256", "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    assert not Digest.is_valid_hexdigest("sha256", "Pe3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85")


def test_is_valid_algo():
    from fwm.utils.digest import Digest
    assert Digest.is_valid_algo("sha256")
    assert Digest.is_valid_algo("md5")
    assert Digest.is_valid_algo("sha512")
    assert not Digest.is_valid_algo("bad-value")
    assert not Digest.is_valid_algo("")
