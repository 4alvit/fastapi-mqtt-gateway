"""Isolated API fixtures: no broker connection or production credentials."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from fastapi_mqtt_gateway.core.auth import create_access_token
from fastapi_mqtt_gateway.core.config import Settings, get_settings


def _test_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]  # BaseSettings accepts _env_file at runtime.
        _env_file=None,
        api_username="testuser",
        api_password="test-api-password-1234",
        mqtt_username="broker-user",
        mqtt_password="broker-password",
        jwt_secret_key="x" * 32,
        allowed_topic_patterns=["devices/#", "sensors/#"],
        blocked_topic_patterns=["$SYS/#", "admin/#", "sensors/private/#"],
        debug=True,
        rate_limit_enabled=False,
    )


@pytest.fixture
def settings() -> Settings:
    return _test_settings()


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[None]:
    from fastapi_mqtt_gateway import api
    from fastapi_mqtt_gateway.core import auth, config

    get_settings.cache_clear()
    for module in (api, auth, config):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(api.limiter, "enabled", False)
    yield
    get_settings.cache_clear()


@pytest.fixture
def valid_token(settings: Settings) -> str:
    return create_access_token(data={"sub": settings.api_username})


@pytest.fixture
def auth_headers(valid_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {valid_token}"}


@pytest.fixture
def mock_mqtt_client() -> MagicMock:
    client = MagicMock()
    client.subscribe = AsyncMock()
    client.unsubscribe = AsyncMock()
    client.is_connected.return_value = True
    client.message_queue_empty.return_value = True
    client._client.publish.return_value.mid = 42
    callbacks: list[Callable[[str, bytes], None]] = []
    client.add_message_callback.side_effect = callbacks.append
    client.remove_message_callback.side_effect = callbacks.remove

    def emit(topic: str, payload: bytes) -> None:
        for callback in tuple(callbacks):
            callback(topic, payload)

    client.emit = emit
    return client


@pytest.fixture
def app(mock_mqtt_client: MagicMock, settings: Settings) -> FastAPI:
    from fastapi_mqtt_gateway.api import limiter, router
    from fastapi_mqtt_gateway.services.mqtt_service import MQTTService

    app = FastAPI()
    app.include_router(router)
    app.state.limiter = limiter

    def handle_rate_limit(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, RateLimitExceeded):
            raise exc
        return _rate_limit_exceeded_handler(request, exc)

    app.add_exception_handler(RateLimitExceeded, handle_rate_limit)
    app.state.mqtt_client = mock_mqtt_client
    app.state.mqtt_service = MQTTService(mock_mqtt_client, settings)
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
async def async_client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
