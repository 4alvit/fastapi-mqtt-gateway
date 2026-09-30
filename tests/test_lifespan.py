"""Application resource ownership without a live MQTT broker."""

import asyncio
import importlib
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from fastapi_mqtt_gateway.core.config import Settings
from fastapi_mqtt_gateway.mqtt.client import MQTTClient


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError, asyncio.CancelledError])
async def test_failed_startup_releases_client(
    monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    main = importlib.import_module("fastapi_mqtt_gateway.main")
    client = MagicMock(connect=AsyncMock(side_effect=failure), disconnect=AsyncMock())
    monkeypatch.setattr(main, "MQTTClient", lambda settings: client)

    with pytest.raises(failure):
        async with main.lifespan(FastAPI()):
            pytest.fail("Failed startup must not yield a running application")

    client.disconnect.assert_awaited_once()


async def test_lifespan_body_error_releases_client(monkeypatch: pytest.MonkeyPatch) -> None:
    main = importlib.import_module("fastapi_mqtt_gateway.main")
    client = MagicMock(connect=AsyncMock(), disconnect=AsyncMock())
    monkeypatch.setattr(main, "MQTTClient", lambda settings: client)

    with pytest.raises(RuntimeError, match="application failure"):
        async with main.lifespan(FastAPI()):
            raise RuntimeError("application failure")

    client.disconnect.assert_awaited_once()


async def test_overlapping_apps_release_their_own_client(monkeypatch: pytest.MonkeyPatch) -> None:
    main = importlib.import_module("fastapi_mqtt_gateway.main")
    first = MagicMock(connect=AsyncMock(), disconnect=AsyncMock())
    second = MagicMock(connect=AsyncMock(), disconnect=AsyncMock())
    monkeypatch.setattr(main, "MQTTClient", MagicMock(side_effect=[first, second]))
    first_app, second_app = FastAPI(), FastAPI()

    async with main.lifespan(first_app):
        async with main.lifespan(second_app):
            assert first_app.state.mqtt_client is first
            assert second_app.state.mqtt_client is second
        second.disconnect.assert_awaited_once()
        first.disconnect.assert_not_awaited()

    first.disconnect.assert_awaited_once()
    second.disconnect.assert_awaited_once()


async def test_network_thread_join_does_not_stall_event_loop(settings: Settings) -> None:
    client = MQTTClient(settings)
    broker = MagicMock()
    client._client = broker
    client._connected.set()
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    release_stop = threading.Event()

    def loop_stop() -> None:
        loop.call_soon_threadsafe(stopping.set)
        release_stop.wait(timeout=1)

    broker.loop_stop.side_effect = loop_stop
    disconnect = asyncio.create_task(client.disconnect())
    try:
        await asyncio.wait_for(stopping.wait(), timeout=2)
        assert not disconnect.done(), "Event loop stalled until the network thread stopped"
    finally:
        release_stop.set()
        await disconnect

    broker.disconnect.assert_called_once()
    assert not client._connected.is_set()


async def test_cancelled_shutdown_finishes_join_and_disconnect(settings: Settings) -> None:
    client = MQTTClient(settings)
    broker = MagicMock()
    client._client = broker
    client._connected.set()
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    release_stop = threading.Event()

    def loop_stop() -> None:
        loop.call_soon_threadsafe(stopping.set)
        release_stop.wait(timeout=2)

    broker.loop_stop.side_effect = loop_stop
    disconnect = asyncio.create_task(client.disconnect())
    try:
        await asyncio.wait_for(stopping.wait(), timeout=2)
        disconnect.cancel()
        await asyncio.sleep(0)
        assert not disconnect.done()
        broker.disconnect.assert_not_called()
    finally:
        release_stop.set()
        with pytest.raises(asyncio.CancelledError):
            await disconnect

    broker.disconnect.assert_called_once()
    assert not client._connected.is_set()
