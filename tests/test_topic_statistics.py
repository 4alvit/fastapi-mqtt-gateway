"""Exercise topic statistics through the application lifespan and HTTP API."""

import asyncio
import importlib
import time
from collections.abc import Iterator
from unittest.mock import MagicMock

import paho.mqtt.client as mqtt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_mqtt_gateway.core.config import Settings
from fastapi_mqtt_gateway.mqtt.client import MQTTClient
from fastapi_mqtt_gateway.services.mqtt_service import MQTTService


@pytest.fixture
def statistics_app(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> tuple[FastAPI, MagicMock]:
    main = importlib.import_module("fastapi_mqtt_gateway.main")
    settings.mqtt_queue_size = 2
    settings.max_message_bytes = 8
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    broker = MagicMock()
    broker.is_connected.return_value = True
    broker.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)

    async def connect(client: MQTTClient) -> None:
        client._client = broker
        client._event_loop = asyncio.get_running_loop()
        client._connected.set()

    monkeypatch.setattr(MQTTClient, "connect", connect)
    return main.create_app(), broker


@pytest.fixture
def statistics_client(
    statistics_app: tuple[FastAPI, MagicMock],
) -> Iterator[tuple[TestClient, MQTTClient, MQTTService]]:
    application, broker = statistics_app
    with TestClient(application) as client:
        mqtt_client = application.state.mqtt_client
        service = application.state.mqtt_service
        yield client, mqtt_client, service
    assert mqtt_client._packet_callbacks == []
    assert mqtt_client._message_callbacks == []
    assert not mqtt_client.is_connected()
    broker.loop_stop.assert_called_once()
    broker.disconnect.assert_called_once()


def deliver(
    client: TestClient,
    mqtt_client: MQTTClient,
    topic: str,
    payload: bytes,
    qos: int = 0,
    retain: bool = False,
) -> None:
    assert client.portal is not None
    client.portal.call(mqtt_client._deliver_message, topic, payload, qos, retain)


def test_normal_lifespan_updates_detail_and_list_with_packet_metadata(
    statistics_client: tuple[TestClient, MQTTClient, MQTTService], auth_headers: dict[str, str]
) -> None:
    client, mqtt_client, _ = statistics_client
    topic = "sensors/demo"
    assert (
        client.post(
            "/mqtt/subscribe", json={"topic": topic, "qos": 2}, headers=auth_headers
        ).status_code
        == 200
    )
    before = time.time()
    deliver(client, mqtt_client, topic, b"21.5", qos=1, retain=True)
    first = client.get(f"/mqtt/topics/{topic}", headers=auth_headers)
    assert first.status_code == 200
    assert first.json()["message_count"] == 1
    assert first.json()["last_message"]["qos"] == 1
    assert first.json()["last_message"]["retain"] is True

    deliver(client, mqtt_client, topic, b"22.0", qos=2, retain=False)
    detail = client.get(f"/mqtt/topics/{topic}", headers=auth_headers)
    after = time.time()
    data = detail.json()
    assert detail.status_code == 200
    assert data["topic"] == topic and data["message_count"] == 2
    assert data["last_message"] == {
        "topic": topic,
        "payload": "22.0",
        "qos": 2,
        "retain": False,
        "timestamp": data["last_message"]["timestamp"],
    }
    assert before <= data["last_message"]["timestamp"] <= after
    listing = client.get("/mqtt/topics", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json() == [data]
    assert mqtt_client._message_queue.get_nowait() == (topic, b"21.5")
    assert mqtt_client._message_queue.get_nowait() == (topic, b"22.0")
    assert len(mqtt_client._packet_callbacks) == 1
    assert client.get(f"/mqtt/topics/{topic}").status_code == 401
    assert client.get("/mqtt/topics").status_code == 401


def test_topic_cache_is_bounded_and_refreshes_recent_delivery(
    statistics_client: tuple[TestClient, MQTTClient, MQTTService], auth_headers: dict[str, str]
) -> None:
    client, mqtt_client, service = statistics_client
    topics = ["sensors/a", "sensors/b", "sensors/c"]
    for topic in topics:
        assert (
            client.post("/mqtt/subscribe", json={"topic": topic}, headers=auth_headers).status_code
            == 200
        )
    for topic in [topics[0], topics[1], topics[0], topics[2]]:
        deliver(client, mqtt_client, topic, b"value")
    listing = client.get("/mqtt/topics", headers=auth_headers).json()
    assert [item["message_count"] for item in listing] == [2, 0, 1]
    assert listing[1]["last_message"] is None
    assert len(service._topic_statistics) == 2
    deliver(client, mqtt_client, topics[1], b"new")
    assert (
        client.get(f"/mqtt/topics/{topics[1]}", headers=auth_headers).json()["message_count"] == 1
    )
    assert len(service._topic_statistics) == 2


def test_wildcard_deliveries_keep_only_the_newest_bounded_topic_entries(
    statistics_client: tuple[TestClient, MQTTClient, MQTTService], auth_headers: dict[str, str]
) -> None:
    client, mqtt_client, service = statistics_client
    assert (
        client.post(
            "/mqtt/subscribe", json={"topic": "devices/#"}, headers=auth_headers
        ).status_code
        == 200
    )
    for index in range(100):
        deliver(client, mqtt_client, f"devices/{index}", b"value")
        assert len(service._topic_statistics) <= 2
    assert list(service._topic_statistics) == ["devices/98", "devices/99"]
    assert client.get("/mqtt/topics", headers=auth_headers).json() == [
        {"topic": "devices/#", "message_count": 0, "last_message": None}
    ]


def test_invalid_or_forbidden_packets_do_not_displace_authorized_statistics(
    statistics_client: tuple[TestClient, MQTTClient, MQTTService], auth_headers: dict[str, str]
) -> None:
    client, mqtt_client, service = statistics_client
    topic = "sensors/demo"
    assert (
        client.post("/mqtt/subscribe", json={"topic": topic}, headers=auth_headers).status_code
        == 200
    )
    deliver(client, mqtt_client, topic, b"accepted")
    original = client.get(f"/mqtt/topics/{topic}", headers=auth_headers).json()
    for forbidden in ("admin/secret", "sensors/private/key", "sensors/+/bad", "sensors/\x00bad"):
        deliver(client, mqtt_client, forbidden, b"private")
    deliver(client, mqtt_client, topic, b"oversized")
    assert client.get(f"/mqtt/topics/{topic}", headers=auth_headers).json() == original
    assert list(service._topic_statistics) == [topic]
    assert (
        client.post(
            "/mqtt/subscribe", json={"topic": "sensors/private/key"}, headers=auth_headers
        ).status_code
        == 403
    )
    deliver(client, mqtt_client, topic, b"\xffx")
    latest = client.get(f"/mqtt/topics/{topic}", headers=auth_headers).json()
    assert latest["message_count"] == 2
    assert latest["last_message"]["payload"] == "\ufffdx"


def test_shutdown_removes_handler_before_client_disconnect(
    statistics_app: tuple[FastAPI, MagicMock], auth_headers: dict[str, str]
) -> None:
    application, broker = statistics_app
    with TestClient(application) as client:
        mqtt_client = application.state.mqtt_client
        service = application.state.mqtt_service
        topic = "sensors/demo"
        assert (
            client.post("/mqtt/subscribe", json={"topic": topic}, headers=auth_headers).status_code
            == 200
        )
        deliver(client, mqtt_client, topic, b"before")
        before = client.get(f"/mqtt/topics/{topic}", headers=auth_headers).json()
        assert before["message_count"] == 1

        def disconnect() -> None:
            assert mqtt_client._packet_callbacks == []
            mqtt_client._deliver_message(topic, b"after")

        broker.disconnect.side_effect = disconnect
    assert asyncio.run(service.get_topic_info(topic)).model_dump() == before
    assert mqtt_client._packet_callbacks == []
    broker.disconnect.assert_called_once()


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError, asyncio.CancelledError])
async def test_startup_failure_removes_registered_statistics_handler(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, failure: type[BaseException]
) -> None:
    main = importlib.import_module("fastapi_mqtt_gateway.main")
    client = MQTTClient(settings)
    monkeypatch.setattr(main, "MQTTClient", lambda _: client)

    async def fail_connect() -> None:
        assert len(client._packet_callbacks) == 1
        raise failure

    monkeypatch.setattr(client, "connect", fail_connect)
    with pytest.raises(failure):
        async with main.lifespan(FastAPI()):
            pytest.fail("Failed startup must not yield")
    assert client._packet_callbacks == []


async def test_lifespan_body_error_unregisters_before_disconnect(
    statistics_app: tuple[FastAPI, MagicMock],
) -> None:
    main = importlib.import_module("fastapi_mqtt_gateway.main")
    application, broker = statistics_app
    with pytest.raises(RuntimeError, match="synthetic application failure"):
        async with main.lifespan(application):
            client = application.state.mqtt_client
            assert len(client._packet_callbacks) == 1
            raise RuntimeError("synthetic application failure")
    assert client._packet_callbacks == []
    broker.disconnect.assert_called_once()
