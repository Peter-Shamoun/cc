"""Opaque, non-PHI identifiers (time-ordered, random tail)."""
from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"


def new_id(prefix: str = "") -> str:
    ms = int(time.time() * 1000)
    head = ""
    for _ in range(9):
        head = _ALPHABET[ms % 32] + head
        ms //= 32
    tail = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    return f"{prefix}{head}{tail}"
