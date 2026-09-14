"""Password handling, on both sides of the door.

Two unrelated things live here because both are "authentication":

* :func:`create_auth` / :func:`verify_password` mirror the PBKDF2 scheme the
  browser application uses for workspace profiles. The server never checks
  these during normal operation -- profile roles are enforced in the client --
  but reproducing the scheme lets an administrator locked out of a workspace
  reset a profile password from the command line without hand-editing JSON.

* :func:`gate_enabled` and friends back the optional server passphrase, which
  is a real boundary: it decides who may reach the app and the API at all.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from typing import Any

#: Matches the browser's PASSWORD_ITERATIONS so hashes stay interchangeable.
PASSWORD_ITERATIONS = 120_000
ALGORITHM = "PBKDF2-SHA256"
SALT_BYTES = 16
KEY_BYTES = 32

_SALT_PATTERN = re.compile(r"^[A-Za-z0-9+/]{22}==$")
_HASH_PATTERN = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def password_hash(password: str, salt: str, iterations: int = PASSWORD_ITERATIONS) -> str:
    """Derive the base64 hash the browser would derive for the same inputs."""
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        base64.b64decode(salt),
        iterations,
        dklen=KEY_BYTES,
    )
    return _b64(derived)


def create_auth(password: str, *, initial: bool = False, now: str | None = None) -> dict[str, Any]:
    """Build a profile ``auth`` block in exactly the client's shape."""
    salt = _b64(os.urandom(SALT_BYTES))
    return {
        "algorithm": ALGORITHM,
        "iterations": PASSWORD_ITERATIONS,
        "salt": salt,
        "hash": password_hash(password, salt),
        "isInitial": initial,
        "updatedAt": now,
    }


def valid_auth(auth: Any) -> bool:
    """Match the client's ``validAuth``: a block it will accept on load."""
    return (
        isinstance(auth, dict)
        and auth.get("algorithm") == ALGORITHM
        and isinstance(auth.get("iterations"), int)
        and 100_000 <= auth["iterations"] <= 600_000
        and isinstance(auth.get("salt"), str)
        and bool(_SALT_PATTERN.match(auth["salt"]))
        and isinstance(auth.get("hash"), str)
        and bool(_HASH_PATTERN.match(auth["hash"]))
    )


def verify_password(auth: Any, password: str) -> bool:
    """Constant-time check of ``password`` against a profile's auth block."""
    if not valid_auth(auth):
        return False
    candidate = password_hash(password, auth["salt"], auth["iterations"])
    return hmac.compare_digest(candidate, auth["hash"])


# --------------------------------------------------------------- server gate


def gate_enabled(config: Any) -> bool:
    """True when a server passphrase has been configured."""
    return bool(config.get("OKR_PASSPHRASE_HASH"))


def make_gate_secret(passphrase: str) -> dict[str, Any]:
    """Hash a server passphrase for storage in the app config."""
    return create_auth(passphrase)


def check_gate(config: Any, passphrase: str) -> bool:
    """Verify a submitted server passphrase."""
    return verify_password(config.get("OKR_PASSPHRASE_HASH"), passphrase)
