"""Auth tests: login, JWT, fail-closed on missing/invalid token."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from fastapi_mqtt_gateway.core.auth import (
    create_access_token,
    get_current_user,
    verify_token,
)
from fastapi_mqtt_gateway.core.config import Settings


class TestLogin:
    def test_login_success(self, client: TestClient) -> None:
        resp = client.post(
            "/auth/token", data={"username": "testuser", "password": "test-api-password-1234"}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 30 * 60

    def test_login_wrong_password(self, client: TestClient) -> None:
        resp = client.post("/auth/token", data={"username": "testuser", "password": "wrong"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"

    def test_login_wrong_username(self, client: TestClient) -> None:
        resp = client.post(
            "/auth/token", data={"username": "nobody", "password": "test-api-password-1234"}
        )
        assert resp.status_code == 401

    def test_login_missing_password(self, client: TestClient) -> None:
        resp = client.post("/auth/token", data={"username": "testuser"})
        assert resp.status_code == 422


class TestProtectedEndpoint:
    """Endpoints must fail closed when auth missing/invalid."""

    def test_no_auth_header_401(self, client: TestClient) -> None:
        resp = client.post(
            "/mqtt/publish",
            json={"topic": "devices/x", "payload": "y"},
        )
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_invalid_token_401(self, client: TestClient) -> None:
        resp = client.post(
            "/mqtt/publish",
            json={"topic": "devices/x", "payload": "y"},
            headers={"Authorization": "Bearer garbage"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid token"

    def test_malformed_scheme_401(self, client: TestClient) -> None:
        resp = client.post(
            "/mqtt/publish",
            json={"topic": "devices/x", "payload": "y"},
            headers={"Authorization": "NotBearer xyz"},
        )
        assert resp.status_code == 401

    def test_valid_token_passes_auth(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/mqtt/publish", json={"topic": "devices/x", "payload": "y"}, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestCreateAccessToken:
    def test_round_trip(self, settings: Settings) -> None:

        token = create_access_token(data={"sub": "alice"})
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        assert payload["sub"] == "alice"
        assert "exp" in payload

    def test_custom_expires_delta(self, settings: Settings) -> None:

        token = create_access_token(
            data={"sub": "bob"},
            expires_delta=timedelta(minutes=5),
        )
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        exp = datetime.fromtimestamp(payload["exp"], UTC)
        delta = (exp - datetime.now(UTC)).total_seconds()
        assert 4 * 60 < delta < 6 * 60


class TestVerifyToken:
    def test_valid(self, valid_token: str) -> None:
        assert verify_token(valid_token) is not None

    def test_garbage(self) -> None:
        assert verify_token("garbage") is None

    def test_wrong_secret(self, settings: Settings) -> None:
        bad = jwt.encode({"sub": "x"}, "y" * 32, algorithm=settings.jwt_algorithm)
        assert verify_token(bad) is None


class TestGetCurrentUser:
    """Auth dependency: missing creds = 401, valid = user, invalid = 401."""

    async def test_no_credentials_raises_401(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(credentials=None)
        assert exc.value.status_code == 401
        assert exc.value.headers is not None
        assert exc.value.headers.get("WWW-Authenticate") == "Bearer"

    async def test_invalid_token_raises_401(self) -> None:
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad")
        with pytest.raises(HTTPException) as exc:
            await get_current_user(credentials=creds)
        assert exc.value.status_code == 401

    async def test_valid_token_returns_user(self, valid_token: str) -> None:
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=valid_token)
        user = await get_current_user(credentials=creds)
        assert user.username == "testuser"


class TestSettingsDefaults:
    def test_missing_credentials_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            # Exercise invalid input; the inferred signature omits BaseSettings runtime keywords.
            Settings(_env_file=None)  # type: ignore[call-arg]

    @pytest.mark.parametrize("value", ["", " " * 32, "change-me-in-production-use-strong-secret"])
    def test_placeholder_secret_rejected(self, value: str) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(  # type: ignore[call-arg]  # BaseSettings accepts _env_file at runtime.
                _env_file=None, api_username="admin", api_password="a" * 20, jwt_secret_key=value
            )
