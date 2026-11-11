"""Shared, lossless validation for native observation identifiers."""
import re


def identifier(value):
    """Preserve a valid identifier verbatim; reject rather than canonicalize."""
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,200}', value) else None
