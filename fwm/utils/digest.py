import hashlib
import os
import string
from collections import defaultdict
from typing import Dict


class Digest:
    digest_size_bytes: Dict[str, int] = {}
    cache: Dict[str, Dict[str, str]] = defaultdict(dict)

    @classmethod
    def digest_file(cls, algo: str, fpath: str, use_cache: bool = False) -> str:
        cache = cls.cache[algo]
        fpath_abs = os.path.abspath(fpath)

        if use_cache and fpath_abs in cache:
            return cache[fpath_abs]

        with open(fpath, 'rb', buffering=0) as fp:
            digest = hashlib.file_digest(fp, algo).hexdigest()  # type: ignore

        cache[fpath_abs] = digest
        return digest

    @classmethod
    def digest_data(cls, algo: str, data: bytes) -> str:
        h = hashlib.new(algo)
        h.update(data)
        return h.hexdigest()

    @classmethod
    def get_digest_size_bytes(cls, algo: str) -> int:
        if algo not in cls.digest_size_bytes:
            cls.digest_size_bytes[algo] = hashlib.new(algo).digest_size
        return cls.digest_size_bytes[algo]

    @classmethod
    def is_valid_hexdigest(cls, algo: str, h: str) -> bool:
        return len(h) == cls.get_digest_size_bytes(algo) * 2 and all(_ in string.hexdigits for _ in h)

    @staticmethod
    def is_valid_algo(algo: str) -> bool:
        return algo in hashlib.algorithms_available
