from datetime import datetime
from typing import Optional

import peewee


class TimestampTzField(peewee.Field):
    """
    A timestamp field that supports a timezone by serializing the value
    with isoformat.
    https://compileandrun.com/python-peewee-timezone-aware-datetime/
    """

    field_type = 'TEXT'  # This is how the field appears in Sqlite

    def db_value(self, value: datetime) -> Optional[str]:
        if value:
            return value.isoformat()
        return None

    def python_value(self, value: str) -> Optional[datetime]:
        if value:
            return datetime.fromisoformat(value)
        return None
