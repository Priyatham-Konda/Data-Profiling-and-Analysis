"""Password hashing with scrypt from the standard library.

scrypt is memory-hard, which is what makes guessing on GPUs expensive, and
taking it from hashlib adds no dependency to license-check (see
requirements.txt). The cost parameters are stored inside each hash, so
raising them later leaves existing passwords verifiable.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from functools import cache

_ALGORITHM = "scrypt"
_N, _R, _P = 2**14, 8, 1          # ~16 MB and ~50 ms per hash
_SALT_BYTES = 16
_KEY_BYTES = 64


def _derive(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
        maxmem=256 * n * r, dklen=length,
    )


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$key`, salt and key in base64."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _derive(password, salt, _N, _R, _P, _KEY_BYTES)
    encoded = [base64.b64encode(part).decode("ascii") for part in (salt, key)]
    return "$".join([_ALGORITHM, str(_N), str(_R), str(_P), *encoded])


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt, key = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        expected = base64.b64decode(key)
        actual = _derive(password, base64.b64decode(salt), int(n), int(r), int(p),
                         len(expected))
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


@cache
def dummy_hash() -> str:
    """Verified against when the email is unknown, so a wrong email takes as
    long to reject as a wrong password and response times don't reveal which
    addresses have accounts."""
    return hash_password(secrets.token_urlsafe(16))
