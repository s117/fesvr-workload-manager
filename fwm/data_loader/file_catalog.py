import json
from datetime import datetime
from typing import Annotated, List

import pydantic
from annotated_types import Gt
from pydantic import model_validator

from fwm.utils.digest import Digest


class FileItem(pydantic.BaseModel):
    # File path.
    path: str

    # The actual size of the files in bytes.
    # Note: this is not the allocated disk space, which is a variable of the actual size and file system.
    actual_size: Annotated[int, Gt(0)]

    # digest of the file.
    digest: str


class DirItem(pydantic.BaseModel):
    # Dir path.
    path: str


class FileCatalog(pydantic.BaseModel):
    # When is the catalog created
    creation_time: datetime

    # Digest algorithm used in this catalog
    digest_algo: str

    # List of files and their digests
    files: List[FileItem]

    # List of dirs
    dirs: List[DirItem]

    @model_validator(mode='after')
    def validate_catalog(self):
        assert Digest.is_valid_algo(
            self.digest_algo), f"File catalog was created with an unsupported digest algorithm {self.digest_algo}."
        for file in self.files:
            assert Digest.is_valid_hexdigest(self.digest_algo, file.digest), \
                f"File catalog contains invalid {self.digest_algo} digest:\n" \
                f"  - File: {file.path}\n" \
                f"  - Invalid digest: {file.digest}\n"
        return self


def load_file_catalog_json(fc_json: str) -> FileCatalog:
    raw_catalog = json.loads(fc_json)
    return FileCatalog(**raw_catalog)
