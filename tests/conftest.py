from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from fastapi_mqtt_gateway.core.auth import create_access_token
from fastapi_mqtt_gateway.core.config import Settings, get_settings


def _test_settings() -> Settings:
    return Settings(
        mqtt_username="testuser",
        mqtt_password="testpass",
        jwt_secret_key="x" * 32,
        jwt_access_token_expire_minutes=30,
        allowed_topic_patterns=["devices/#", "sensors/#"],
        blocked_topic_patterns=["$SYS/#", "admin/#"],
        debug=True,
        rate_limit_enabled=False,
    )


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Clear lru_cache so patches take effect."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Test settings with known credentials."""
    return _test_settings()


@pytest.fixture
def valid_token(settings: Settings) -> str:
    """Valid JWT token for testuser."""
    return create_access_token(
        data={"sub": "testuser"},
        expires_delta=timedelta(minutes=30),
    )


@pytest.fixture
def auth_headers(valid_token: str) -> dict[str, str]:
    """Auth headers with valid Bearer token."""
    return {"Authorization": f"Bearer {valid_token}"}


@pytest.fixture
def mock_mqtt_client() -> MagicMock:
    """Mock MQTTClient with all methods mocked."""
    client = MagicMock()
    client._client = MagicMock()
    client.is_connected.return_value = True
    client.message_queue_empty.return_value = False
    return client


@pytest.fixture
def app(mock_mqtt_client: MagicMock) -> Iterator[FastAPI]:
    """App wired to mock MQTT client/service, test settings.

    Settings patched in BOTH core.config (login endpoint) AND core.auth
    (module-level SECRET_KEY). Lifespan bypassed by constructing app
    directly without ASGI lifespan. api.get_mqtt_client/get_mqtt_service
    resolve app.state via import of main.app — override with
    dependency_overrides instead.
    """
    from fastapi_mqtt_gateway.api import get_mqtt_client, get_mqtt_service
    from fastapi_mqtt_gateway.services.mqtt_service import MQTTService

    test_settings = _test_settings()
    mock_service = MagicMock(spec=MQTTService)

    with (
        patch("fastapi_mqtt_gateway.core.config.get_settings", return_value=test_settings),
        patch("fastapi_mqtt_gateway.core.auth.get_settings", return_value=test_settings),
        patch("fastapi_mqtt_gateway.main.get_settings", return_value=test_settings),
        patch("fastapi_mqtt_gateway.core.auth.SECRET_KEY", test_settings.jwt_secret_key),
        patch("fastapi_mqtt_gateway.core.auth.ALGORITHM", test_settings.jwt_algorithm),
        patch(
            "fastapi_mqtt_gateway.core.auth.ACCESS_TOKEN_EXPIRE_MINUTES",
            test_settings.jwt_access_token_expire_minutes,
        ),
    ):
        # api/__init__.py binds get_settings at import; rebind the module-level name
        # so login() sees test settings instead of the real .env-backed one.
        from fastapi_mqtt_gateway import api as _api_module

        _api_module.get_settings = lambda: test_settings  # type: ignore[assignment]
        from fastapi_mqtt_gateway.api import router as api_router

        app = FastAPI(title=test_settings.app_name, version=test_settings.app_version)
        app.include_router(api_router)
        app.dependency_overrides[get_mqtt_client] = lambda: mock_mqtt_client
        app.dependency_overrides[get_mqtt_service] = lambda: mock_service
        # Store service as an attribute the test can configure
        app.state.mqtt_service = mock_service
        app.state.mqtt_client = mock_mqtt_client
        yield app


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
async def async_client(app) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
