"""Real lifespan and packet delivery with an in-process broker, never a network broker."""

import asyncio
import contextlib
import importlib
import json
import time
from typing import Any
from unittest.mock import MagicMock

import paho.mqtt.client as mqtt
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.types import Message, Scope
from starlette.websockets import WebSocketDisconnect

from fastapi_mqtt_gateway.core.config import Settings
from fastapi_mqtt_gateway.mqtt.client import MQTTClient
from fastapi_mqtt_gateway.services.mqtt_service import MQTTService


@pytest.fixture
def metadata_app(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> tuple[FastAPI, MagicMock]:
    main = importlib.import_module("fastapi_mqtt_gateway.main")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    settings.max_message_bytes = 8
    broker = MagicMock()
    broker.is_connected.return_value = True
    broker.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    broker.unsubscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)

    async def connect(client: MQTTClient) -> None:
        client._client = broker
        client._event_loop = asyncio.get_running_loop()
        client._connected.set()

    monkeypatch.setattr(MQTTClient, "connect", connect)
    return main.create_app(), broker


@pytest.mark.parametrize("authentication", ["header", "frame"])
def test_stream_preserves_packets_with_shared_rest_subscription(
    metadata_app: tuple[FastAPI, MagicMock],
    auth_headers: dict[str, str],
    valid_token: str,
    authentication: str,
) -> None:
    application, broker = metadata_app
    topic = "devices/demo"
    with TestClient(application) as client:
        assert client.portal is not None
        mqtt_client: MQTTClient = application.state.mqtt_client
        service: MQTTService = application.state.mqtt_service
        assert (
            client.post(
                "/mqtt/subscribe", headers=auth_headers, json={"topic": topic, "qos": 2}
            ).status_code
            == 200
        )
        with client.websocket_connect(
            f"/ws?topics={topic}", headers=auth_headers if authentication == "header" else {}
        ) as websocket:
            if authentication == "frame":
                websocket.send_json({"type": "auth", "token": valid_token})
            assert websocket.receive_json() == {"type": "ready", "topics": [topic]}
            broker.subscribe.assert_called_once_with(topic, qos=2)
            for payload, qos, retain in ((b"21.5", 2, True), (b"22.\xff", 1, False)):
                before = time.time()
                # Neither unrequested topics nor oversized messages may enter the stream.
                client.portal.call(mqtt_client._deliver_message, "devices/other", b"omit", 2, True)
                client.portal.call(mqtt_client._deliver_message, topic, b"oversized", 2, True)
                client.portal.call(mqtt_client._deliver_message, topic, payload, qos, retain)
                received = websocket.receive_json()
                assert received == {
                    "topic": topic,
                    "payload": payload.decode(errors="replace"),
                    "qos": qos,
                    "retain": retain,
                    "timestamp": received["timestamp"],
                }
                assert before <= received["timestamp"] <= time.time()
                rest = client.get(f"/mqtt/topics/{topic}", headers=auth_headers).json()
                assert rest["last_message"]["qos"] == qos
                assert rest["last_message"]["retain"] is retain
            assert len(mqtt_client._packet_callbacks) == 2
            assert mqtt_client._message_callbacks == []
        assert mqtt_client._packet_callbacks == [service.handle_message]
        assert service._owners == {topic: {"rest": 2}}
        broker.unsubscribe.assert_not_called()
        assert (
            client.post(
                "/mqtt/unsubscribe", headers=auth_headers, json={"topic": topic}
            ).status_code
            == 200
        )
        broker.unsubscribe.assert_called_once_with(topic)
    assert mqtt_client._packet_callbacks == []
    broker.disconnect.assert_called_once()


@pytest.mark.parametrize("failure", ["subscription", "ready", "message"])
def test_stream_errors_remove_callback_and_release_acquired_topics(
    metadata_app: tuple[FastAPI, MagicMock],
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    application, broker = metadata_app
    original_send = WebSocket.send_json

    async def send(websocket: WebSocket, data: Any, mode: str = "text") -> None:
        if (failure == "ready" and data.get("type") == "ready") or (
            failure == "message" and "payload" in data
        ):
            raise RuntimeError("synthetic send failure")
        await original_send(websocket, data, mode)

    monkeypatch.setattr(WebSocket, "send_json", send)
    if failure == "subscription":
        broker.subscribe.side_effect = [(mqtt.MQTT_ERR_SUCCESS, 1), (mqtt.MQTT_ERR_NO_CONN, 2)]
    with TestClient(application) as client:
        mqtt_client: MQTTClient = application.state.mqtt_client
        service: MQTTService = application.state.mqtt_service
        with client.websocket_connect(
            "/ws?topics=devices/a,devices/b", headers=auth_headers
        ) as websocket:
            if failure == "message":
                assert websocket.receive_json()["type"] == "ready"
                assert len(mqtt_client._packet_callbacks) == 2
                assert client.portal is not None
                client.portal.call(mqtt_client._deliver_message, "devices/a", b"value", 2, True)
            with pytest.raises(WebSocketDisconnect) as exc:
                websocket.receive_json()
            assert exc.value.code == 1011
        assert mqtt_client._packet_callbacks == [service.handle_message]
        assert mqtt_client._message_callbacks == []
        assert not service._owners and not service.get_subscriptions()
        assert broker.unsubscribe.call_count == (1 if failure == "subscription" else 2)
    assert mqtt_client._packet_callbacks == []


@pytest.mark.parametrize("phase", ["subscription", "stream"])
async def test_cancelled_asgi_request_removes_packet_callback_and_owners(
    metadata_app: tuple[FastAPI, MagicMock],
    valid_token: str,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    application, broker = metadata_app
    reached = asyncio.Event()
    incoming: asyncio.Queue[Message] = asyncio.Queue()
    incoming.put_nowait({"type": "websocket.connect"})

    async def send(message: Message) -> None:
        if message["type"] == "websocket.send":
            assert json.loads(message["text"])["type"] == "ready"
            reached.set()

    scope: Scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "scheme": "ws",
        "path": "/ws",
        "root_path": "",
        "query_string": b"topics=devices/a,devices/b",
        "headers": [(b"authorization", f"Bearer {valid_token}".encode())],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "subprotocols": [],
    }
    async with application.router.lifespan_context(application):
        mqtt_client: MQTTClient = application.state.mqtt_client
        service: MQTTService = application.state.mqtt_service
        original_subscribe = mqtt_client.subscribe

        async def subscribe(topic: str, qos: int) -> None:
            if phase == "subscription" and topic == "devices/b":
                reached.set()
                await asyncio.Event().wait()
            await original_subscribe(topic, qos)

        monkeypatch.setattr(mqtt_client, "subscribe", subscribe)
        task = asyncio.create_task(application(scope, incoming.get, send))
        try:
            await asyncio.wait_for(reached.wait(), timeout=2)
            assert len(mqtt_client._packet_callbacks) == 2
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        assert mqtt_client._packet_callbacks == [service.handle_message]
        assert mqtt_client._message_callbacks == []
        assert not service._owners and not service.get_subscriptions()
        assert broker.unsubscribe.call_count == (1 if phase == "subscription" else 2)
    assert mqtt_client._packet_callbacks == []
