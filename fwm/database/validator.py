import string

_VALID_ENTITY_NAME_CHARSET = set(string.ascii_letters + string.digits + '_')


def validate_entity_name(entity_name: str) -> bool:
    for c in entity_name:
        if c not in _VALID_ENTITY_NAME_CHARSET:
            return False
    return True
