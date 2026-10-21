import asyncio
import time
from collections import OrderedDict
from uuid import uuid4

import structlog
from fastapi import HTTPException

from fastapi_mqtt_gateway.core.config import Settings
from fastapi_mqtt_gateway.core.topics import authorize_topic
from fastapi_mqtt_gateway.models import (
    MQTTMessage,
    PublishRequest,
    PublishResponse,
    RetainedMessage,
    RetainedQueryRequest,
    RetainedQueryResponse,
    SubscribeRequest,
    SubscribeResponse,
    TopicInfo,
    UnsubscribeRequest,
    UnsubscribeResponse,
)
from fastapi_mqtt_gateway.mqtt.client import MQTTClient, ReceivedMessage

logger = structlog.get_logger()


class MQTTService:
    def __init__(self, client: MQTTClient, settings: Settings):
        self.client = client
        self.settings = settings
        self._subscriptions: dict[str, int] = {}
        self._owners: dict[str, dict[str, int]] = {}
        self._subscription_lock = asyncio.Lock()
        self._topic_statistics: OrderedDict[str, TopicInfo] = OrderedDict()

    async def publish(self, request: PublishRequest) -> PublishResponse:
        authorize_topic(request.topic, self.settings)
        try:
            message_id = await self.client.publish(
                request.topic,
                request.payload.encode() if isinstance(request.payload, str) else request.payload,
                qos=request.qos,
                retain=request.retain,
            )
            return PublishResponse(
                success=True,
                message_id=message_id,
                topic=request.topic,
            )
        except Exception as e:
            logger.error("Publish failed", topic=request.topic, error=str(e))
            return PublishResponse(success=False, topic=request.topic)

    async def acquire_subscription(
        self, topic: str, qos: int, owner: str, *, refresh: bool = False
    ) -> None:
        authorize_topic(topic, self.settings, subscription=True)
        async with self._subscription_lock:
            owners = dict(self._owners.get(topic, {}))
            owners[owner] = qos
            effective_qos = max(owners.values())
            if (
                refresh
                or topic not in self._subscriptions
                or effective_qos != self._subscriptions[topic]
            ):
                await self.client.subscribe(topic, effective_qos)
            self._owners[topic] = owners
            self._subscriptions[topic] = effective_qos

    async def release_subscription(self, topic: str, owner: str) -> None:
        async with self._subscription_lock:
            owners = self._owners.get(topic)
            if not owners or owner not in owners:
                return
            if len(owners) > 1:
                del owners[owner]
                return
            # Remove desired state even while disconnected; reconnect must not
            # resurrect subscriptions whose last consumer has gone away.
            try:
                await self.client.unsubscribe(topic)
            finally:
                self._owners.pop(topic, None)
                self._subscriptions.pop(topic, None)

    async def subscribe(self, request: SubscribeRequest) -> SubscribeResponse:
        await self.acquire_subscription(request.topic, request.qos, "rest")
        return SubscribeResponse(success=True, topic=request.topic, qos=request.qos)

    async def unsubscribe(self, request: UnsubscribeRequest) -> UnsubscribeResponse:
        authorize_topic(request.topic, self.settings, subscription=True)
        await self.release_subscription(request.topic, "rest")
        return UnsubscribeResponse(success=True, topic=request.topic)

    async def query_retained(self, request: RetainedQueryRequest) -> RetainedQueryResponse:
        authorize_topic(request.topic, self.settings)
        owner = f"retained:{uuid4()}"
        future: asyncio.Future[ReceivedMessage] = asyncio.get_running_loop().create_future()

        def receive(message: ReceivedMessage) -> None:
            if message.topic == request.topic and message.retain and not future.done():
                future.set_result(message)

        # Register before SUBSCRIBE: the retained response may arrive immediately.
        self.client.add_packet_callback(receive)
        try:
            async with asyncio.timeout(self.settings.retained_query_timeout):
                # Re-subscribe even when another owner already holds this topic:
                # MQTT's default retain handling requests its current retained value.
                await self.acquire_subscription(request.topic, 0, owner, refresh=True)
                message = await future
            return RetainedQueryResponse(
                success=True,
                message=RetainedMessage(
                    topic=message.topic,
                    payload=message.payload.decode(),
                    qos=message.qos,
                    retain=message.retain,
                ),
            )
        except TimeoutError:
            return RetainedQueryResponse(success=False, error="Query timeout")
        except Exception as e:
            logger.error("Retained query failed", topic=request.topic, error=str(e))
            return RetainedQueryResponse(success=False, error="Retained query failed")
        finally:
            self.client.remove_packet_callback(receive)
            future.cancel()
            await self.release_subscription(request.topic, owner)

    async def get_topic_info(self, topic: str) -> TopicInfo:
        authorize_topic(topic, self.settings, subscription=True)
        statistics = self._topic_statistics.get(topic)
        return statistics if statistics is not None else TopicInfo(topic=topic, message_count=0)

    def handle_message(self, message: ReceivedMessage) -> None:
        if len(message.payload) > self.settings.max_message_bytes:
            return
        try:
            authorize_topic(message.topic, self.settings)
        except HTTPException:
            return
        msg = MQTTMessage(
            topic=message.topic,
            payload=message.payload.decode(errors="replace"),
            qos=message.qos,
            retain=message.retain,
            timestamp=time.time(),
        )
        previous = self._topic_statistics.pop(message.topic, None)
        if previous is None and len(self._topic_statistics) >= self.settings.mqtt_queue_size:
            self._topic_statistics.popitem(last=False)
        self._topic_statistics[message.topic] = TopicInfo(
            topic=message.topic,
            message_count=previous.message_count + 1 if previous else 1,
            last_message=msg,
        )

    def get_subscriptions(self) -> dict[str, int]:
        return self._subscriptions.copy()

    def is_subscribed(self, topic: str) -> bool:
        return topic in self._subscriptions
