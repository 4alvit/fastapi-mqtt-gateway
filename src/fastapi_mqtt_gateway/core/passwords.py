"""Salted PBKDF2 verifiers; plaintext API passwords are never configuration."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

ITERATIONS = 600_000
_PATTERN = re.compile(r"pbkdf2_sha256\$600000\$([0-9a-f]{32})\$([0-9a-f]{64})")


def validate_password_hash(value: str) -> str:
    """Reject malformed verifiers and attacker-selected work factors."""
    if _PATTERN.fullmatch(value) is None:
        raise ValueError("Configure a PBKDF2 API_PASSWORD_HASH using the password-hash command")
    return value


def hash_password(password: str) -> str:
    """Create a 128-bit random salt and an iterated SHA-256 verifier."""
    if not 16 <= len(password) <= 1024:
        raise ValueError("Password length must be between 16 and 1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify without a plaintext stored secret or an unbounded KDF cost."""
    match = _PATTERN.fullmatch(encoded)
    if match is None or not 1 <= len(password) <= 1024:
        return False
    salt_hex, expected_hex = match.groups()
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), ITERATIONS)
    return hmac.compare_digest(actual, bytes.fromhex(expected_hex))
