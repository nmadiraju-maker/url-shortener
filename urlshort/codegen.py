"""Short-code generation."""

from __future__ import annotations

import secrets
import string

ALPHABET = string.digits + string.ascii_letters  # base62
MIN_LENGTH = 4


def random_code(length: int) -> str:
    """Unpredictable code from a CSPRNG. 62^7 is about 3.5e12 codes at the default length.

    Random rather than sequential so codes cannot be enumerated (privacy) and do not reveal how
    many links exist (business volume).
    """
    if length < MIN_LENGTH:
        raise ValueError(f"code length must be at least {MIN_LENGTH}")
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
