"""Regression coverage for stored-password migration and verifier boundaries."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fastapi_mqtt_gateway.core.config import Settings
from fastapi_mqtt_gateway.core.passwords import (
    hash_password,
    validate_password_hash,
    verify_password,
)


def test_password_hash_uses_unique_salts_and_accepts_only_matching_password() -> None:
    password = "a-test-only-long-password"
    first, second = hash_password(password), hash_password(password)
    assert first != second
    assert password not in first
    assert verify_password(password, first)
    assert verify_password(password, second)
    assert not verify_password(password + "x", first)


@pytest.mark.parametrize(
    "value",
    ["", "plaintext-password", "pbkdf2_sha256$1$00$00", "pbkdf2_sha256$9999999999$00$00"],
)
def test_malformed_or_unsafe_work_factors_fail_closed(value: str) -> None:
    with pytest.raises(ValueError):
        validate_password_hash(value)
    assert not verify_password("a-test-only-long-password", value)


@pytest.mark.parametrize("password", ["short", "x" * 1025])
def test_password_generator_rejects_outside_documented_bounds(password: str) -> None:
    with pytest.raises(ValueError):
        hash_password(password)


def test_plaintext_legacy_configuration_cannot_enable_login() -> None:
    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]  # Exercise old, unsupported configuration.
            _env_file=None,
            api_username="admin",
            api_password="old-plaintext-password",
            jwt_secret_key="x" * 32,
        )


def test_password_verifier_is_not_in_settings_repr(settings: Settings) -> None:
    assert settings.api_password_hash not in repr(settings)
