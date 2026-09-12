"""Delivery regressions without a network broker."""

import asyncio
from unittest.mock import MagicMock

import paho.mqtt.client as mqtt
import pytest

from fastapi_mqtt_gateway.core.config import Settings
from fastapi_mqtt_gateway.models import PublishRequest, RetainedQueryRequest, SubscribeRequest
from fastapi_mqtt_gateway.mqtt.client import MQTTClient
from fastapi_mqtt_gateway.services.mqtt_service import MQTTService


def connected_client(settings: Settings) -> tuple[MQTTClient, MagicMock]:
    client = MQTTClient(settings)
    broker = MagicMock()
    broker.is_connected.return_value = True
    broker.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    client._client = broker
    client._connected.set()
    return client, broker


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [mqtt.MQTT_ERR_NO_CONN, mqtt.MQTT_ERR_QUEUE_SIZE])
async def test_publish_rejection(settings: Settings, code: int) -> None:
    client, broker = connected_client(settings)
    broker.publish.return_value = mqtt.MQTTMessageInfo(42)
    broker.publish.return_value.rc = code
    result = await MQTTService(client, settings).publish(
        PublishRequest(topic="devices/a", payload="on")
    )
    assert not result.success and result.message_id is None


@pytest.mark.asyncio
async def test_disconnected_publish(settings: Settings) -> None:
    client = MQTTClient(settings)
    client._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    result = await MQTTService(client, settings).publish(
        PublishRequest(topic="devices/a", payload="on")
    )
    assert not result.success


@pytest.mark.asyncio
async def test_accepted_publish_contract(settings: Settings) -> None:
    client, broker = connected_client(settings)
    broker.publish.return_value = mqtt.MQTTMessageInfo(42)
    result = await MQTTService(client, settings).publish(
        PublishRequest(topic="devices/a", payload="on", qos=2, retain=True)
    )
    assert result.success and result.message_id == 42
    broker.publish.assert_called_once_with("devices/a", b"on", qos=2, retain=True)


@pytest.mark.asyncio
async def test_delayed_retained_ignores_unrelated_and_live(settings: Settings) -> None:
    settings.retained_query_timeout = 1
    client, _ = connected_client(settings)
    client._event_loop = asyncio.get_running_loop()
    service = MQTTService(client, settings)
    query = asyncio.create_task(service.query_retained(RetainedQueryRequest(topic="devices/a")))
    await asyncio.sleep(0)
    client._deliver_message("devices/other", b"unrelated", retain=True)
    client._deliver_message("devices/a", b"live")
    await asyncio.sleep(0.15)
    assert not query.done()
    packet = mqtt.MQTTMessage(topic=b"devices/a")
    packet.payload, packet.qos, packet.retain = b"retained", 1, True
    client._on_message(MagicMock(), None, packet)
    result = await asyncio.wait_for(query, 1)
    assert result.success and result.message is not None
    assert result.message.payload == "retained"
    assert result.message.qos == 1 and result.message.retain
    assert await client.get_message() == ("devices/other", b"unrelated")
    assert not client._packet_callbacks and not service.get_subscriptions()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_topic", [False, True])
async def test_concurrent_queries(settings: Settings, same_topic: bool) -> None:
    client, _ = connected_client(settings)
    service = MQTTService(client, settings)
    second = "devices/a" if same_topic else "devices/b"
    tasks = [
        asyncio.create_task(service.query_retained(RetainedQueryRequest(topic=topic)))
        for topic in ("devices/a", second)
    ]
    await asyncio.sleep(0)
    client._deliver_message("devices/a", b"first", retain=True)
    if not same_topic:
        client._deliver_message(second, b"second", retain=True)
    results = await asyncio.wait_for(asyncio.gather(*tasks), 1)
    assert all(result.success for result in results)
    assert results[1].message is not None and results[1].message.topic == second
    assert not client._packet_callbacks and not service.get_subscriptions()


@pytest.mark.asyncio
async def test_existing_owner_survives_immediate_retained(settings: Settings) -> None:
    client, broker = connected_client(settings)
    service = MQTTService(client, settings)
    await service.subscribe(SubscribeRequest(topic="devices/a", qos=2))

    def subscribe(topic: str, qos: int) -> tuple[int, int]:
        client._deliver_message(topic, b"immediate", qos=qos, retain=True)
        return mqtt.MQTT_ERR_SUCCESS, 2

    broker.subscribe.side_effect = subscribe
    result = await service.query_retained(RetainedQueryRequest(topic="devices/a"))
    assert result.success
    assert service.get_subscriptions() == {"devices/a": 2}
    broker.subscribe.assert_called_with("devices/a", qos=2)
    broker.unsubscribe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_cleanup(settings: Settings, cancel: bool) -> None:
    settings.retained_query_timeout = 0.02
    client, broker = connected_client(settings)
    service = MQTTService(client, settings)
    query = asyncio.create_task(service.query_retained(RetainedQueryRequest(topic="devices/a")))
    await asyncio.sleep(0)
    if cancel:
        query.cancel()
        with pytest.raises(asyncio.CancelledError):
            await query
    else:
        result = await asyncio.wait_for(query, 1)
        assert not result.success and result.error == "Query timeout"
    assert not client._packet_callbacks and not service.get_subscriptions()
    broker.unsubscribe.assert_called_once_with("devices/a")


@pytest.mark.asyncio
async def test_failed_subscribe_preserves_existing_owner(settings: Settings) -> None:
    client, broker = connected_client(settings)
    service = MQTTService(client, settings)
    await service.subscribe(SubscribeRequest(topic="devices/a"))
    broker.subscribe.return_value = (mqtt.MQTT_ERR_NO_CONN, 2)
    result = await service.query_retained(RetainedQueryRequest(topic="devices/a"))
    assert not result.success and not client._packet_callbacks
    assert service.is_subscribed("devices/a")
    broker.unsubscribe.assert_not_called()
