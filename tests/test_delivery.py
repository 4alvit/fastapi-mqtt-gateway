"""Delivery regressions without a network broker."""

import asyncio
from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock, patch

import paho.mqtt.client as mqtt
import pytest

from fastapi_mqtt_gateway.core.config import Settings
from fastapi_mqtt_gateway.models import PublishRequest, RetainedQueryRequest, SubscribeRequest
from fastapi_mqtt_gateway.mqtt.client import MQTTClient, ReceivedMessage
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


class _InboundPacket:
    """Typed stand-in carrying the four attributes the delivery path reads."""

    def __init__(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        self.topic = topic
        self.payload = payload
        self.qos = qos
        self.retain = retain


class _ScheduledLoop:
    """Records drain callbacks without touching a running event loop."""

    def __init__(self) -> None:
        self.scheduled: list[Callable[[], None]] = []

    def call_soon_threadsafe(self, callback: Callable[[], None], *args: object) -> None:
        if args:
            raise AssertionError("drain scheduling must not bind extra arguments")
        self.scheduled.append(callback)


def _delivery_packet(topic: str, payload: bytes, qos: int, retain: bool) -> _InboundPacket:
    return _InboundPacket(topic, payload, qos, retain)


def _queued_message(client: MQTTClient, topic: str, payload: bytes, qos: int, retain: bool) -> None:
    client._on_message(
        cast(mqtt.Client, None),
        None,
        cast(mqtt.MQTTMessage, _delivery_packet(topic, payload, qos, retain)),
    )


def test_queued_subclass_override_receives_original_arguments(settings: Settings) -> None:
    seen: list[tuple[str, bytes, int, bool]] = []

    class Sub(MQTTClient):
        def _deliver_message(
            self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
        ) -> None:
            seen.append((topic, payload, qos, retain))
            super()._deliver_message(topic, payload, qos, retain)

    client = Sub(settings)
    queue = client._message_queue
    queue_id = id(queue)
    capacity = queue.maxsize
    loop = _ScheduledLoop()
    client._event_loop = cast(asyncio.AbstractEventLoop, loop)
    payload = b"subclass-payload"
    _queued_message(client, "devices/a", payload, 2, True)
    assert len(loop.scheduled) == 1
    loop.scheduled[0]()
    assert seen == [("devices/a", payload, 2, True)]
    assert seen[0][1] is payload
    assert id(client._message_queue) == queue_id
    assert type(client._message_queue) is asyncio.Queue
    assert client._message_queue.maxsize == capacity
    topic, retained = client._message_queue.get_nowait()
    assert topic == "devices/a" and retained is payload
    assert not client._pending_messages and client._dispatch_pending is False


def test_queued_instance_override_receives_original_arguments(settings: Settings) -> None:
    client = MQTTClient(settings)
    queue_id = id(client._message_queue)
    capacity = client._message_queue.maxsize
    loop = _ScheduledLoop()
    client._event_loop = cast(asyncio.AbstractEventLoop, loop)
    payload = b"instance-payload"
    _queued_message(client, "devices/b", payload, 1, False)
    seen: list[tuple[str, bytes, int, bool]] = []
    original = client._deliver_message

    def replacement(topic: str, body: bytes, qos: int = 0, retain: bool = False) -> None:
        seen.append((topic, body, qos, retain))
        original(topic, body, qos, retain)

    with patch.object(client, "_deliver_message", replacement):
        assert len(loop.scheduled) == 1
        loop.scheduled[0]()
        assert seen == [("devices/b", payload, 1, False)]
        assert seen[0][1] is payload
        assert id(client._message_queue) == queue_id
        assert type(client._message_queue) is asyncio.Queue
        assert client._message_queue.maxsize == capacity
        topic, retained = client._message_queue.get_nowait()
        assert topic == "devices/b" and retained is payload
        assert not client._pending_messages and client._dispatch_pending is False


def test_fresh_callback_messages_and_bounded_consumer_keeps_newest(settings: Settings) -> None:
    client = MQTTClient(settings)
    queue = client._message_queue
    capacity = queue.maxsize
    assert capacity >= 2
    queue_id = id(queue)
    assert type(queue) is asyncio.Queue
    for index in range(capacity):
        queue.put_nowait((f"old-{index}", b"old"))
    loop = _ScheduledLoop()
    client._event_loop = cast(asyncio.AbstractEventLoop, loop)
    seen: list[ReceivedMessage] = []
    client.add_packet_callback(seen.append)
    first = b"newest-a"
    second = b"newest-b"
    _queued_message(client, "topic-a", first, 1, True)
    _queued_message(client, "topic-b", second, 2, False)
    assert len(loop.scheduled) == 1
    loop.scheduled[0]()
    assert len(seen) == 2
    assert type(seen[0]).__name__ == "ReceivedMessage"
    assert type(seen[1]).__name__ == "ReceivedMessage"
    assert seen[0] is not seen[1]
    assert seen[0].topic == "topic-a" and seen[0].payload is first
    assert seen[0].qos == 1 and seen[0].retain is True
    assert seen[1].topic == "topic-b" and seen[1].payload is second
    assert seen[1].qos == 2 and seen[1].retain is False
    retained: list[tuple[str, bytes]] = []
    while not queue.empty():
        retained.append(queue.get_nowait())
    assert len(retained) == capacity
    assert retained[-2] == ("topic-a", first) and retained[-2][1] is first
    assert retained[-1] == ("topic-b", second) and retained[-1][1] is second
    assert id(client._message_queue) == queue_id
    assert client._message_queue.maxsize == capacity
    assert not client._pending_messages and client._dispatch_pending is False


def test_reentrant_queued_arrival_is_drained_without_loss(settings: Settings) -> None:
    client = MQTTClient(settings)
    assert client._message_queue.maxsize >= 2
    queue_id = id(client._message_queue)
    loop = _ScheduledLoop()
    client._event_loop = cast(asyncio.AbstractEventLoop, loop)
    second = b"second-payload"
    topics: list[tuple[str, bytes, int, bool]] = []

    def callback(message: ReceivedMessage) -> None:
        topics.append((message.topic, message.payload, message.qos, message.retain))
        if message.topic == "first":
            _queued_message(client, "second", second, 1, False)

    client.add_packet_callback(callback)
    first = b"first-payload"
    _queued_message(client, "first", first, 2, True)
    assert len(loop.scheduled) == 1
    loop.scheduled[0]()
    assert topics == [("first", first, 2, True)]
    assert topics[0][1] is first
    assert len(loop.scheduled) == 2
    assert client._dispatch_pending is True
    loop.scheduled[1]()
    assert topics == [("first", first, 2, True), ("second", second, 1, False)]
    assert topics[1][1] is second
    assert topics[0][0] != topics[1][0]
    retained: list[tuple[str, bytes]] = []
    while not client._message_queue.empty():
        retained.append(client._message_queue.get_nowait())
    assert retained == [("first", first), ("second", second)]
    assert id(client._message_queue) == queue_id
    assert type(client._message_queue) is asyncio.Queue
    assert not client._pending_messages and client._dispatch_pending is False
