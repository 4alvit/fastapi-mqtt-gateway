import asyncio

import structlog

from fastapi_mqtt_gateway.core.config import Settings
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
from fastapi_mqtt_gateway.mqtt.client import MQTTClient

logger = structlog.get_logger()


class MQTTService:
    def __init__(self, client: MQTTClient, settings: Settings):
        self.client = client
        self.settings = settings
        self._subscriptions: dict[str, int] = {}
        self._topic_messages: dict[str, MQTTMessage] = {}
        self._message_counts: dict[str, int] = {}

    async def publish(self, request: PublishRequest) -> PublishResponse:
        try:
            assert self.client._client is not None
            message_info = self.client._client.publish(
                request.topic,
                request.payload.encode() if isinstance(request.payload, str) else request.payload,
                qos=request.qos,
                retain=request.retain,
            )
            return PublishResponse(
                success=True,
                message_id=message_info.mid,
                topic=request.topic,
            )
        except Exception as e:
            logger.error("Publish failed", topic=request.topic, error=str(e))
            return PublishResponse(success=False, topic=request.topic)

    async def subscribe(self, request: SubscribeRequest) -> SubscribeResponse:
        await self.client.subscribe(request.topic, request.qos)
        self._subscriptions[request.topic] = request.qos
        self._message_counts[request.topic] = 0
        return SubscribeResponse(success=True, topic=request.topic, qos=request.qos)

    async def unsubscribe(self, request: UnsubscribeRequest) -> UnsubscribeResponse:
        await self.client.unsubscribe(request.topic)
        self._subscriptions.pop(request.topic, None)
        return UnsubscribeResponse(success=True, topic=request.topic)

    async def query_retained(self, request: RetainedQueryRequest) -> RetainedQueryResponse:
        try:
            await self.client.subscribe(request.topic, qos=0)
            await asyncio.sleep(0.1)

            if not self.client.message_queue_empty():
                topic, payload = await asyncio.wait_for(
                    self.client.get_message(),
                    timeout=self.settings.retained_query_timeout,
                )
                if topic == request.topic:
                    return RetainedQueryResponse(
                        success=True,
                        message=RetainedMessage(
                            topic=topic,
                            payload=payload.decode() if isinstance(payload, bytes) else payload,
                            qos=0,
                            retain=True,
                        ),
                    )

            return RetainedQueryResponse(success=False, error="No retained message found")
        except TimeoutError:
            return RetainedQueryResponse(success=False, error="Query timeout")
        except Exception as e:
            logger.error("Retained query failed", topic=request.topic, error=str(e))
            return RetainedQueryResponse(success=False, error=str(e))

    async def get_topic_info(self, topic: str) -> TopicInfo:
        count = self._message_counts.get(topic, 0)
        last_msg = self._topic_messages.get(topic)
        return TopicInfo(topic=topic, message_count=count, last_message=last_msg)

    def handle_message(self, topic: str, payload: bytes) -> None:
        self._message_counts[topic] = self._message_counts.get(topic, 0) + 1
        msg = MQTTMessage(
            topic=topic,
            payload=payload.decode() if isinstance(payload, bytes) else payload,
            qos=0,
            retain=False,
            timestamp=asyncio.get_event_loop().time(),
        )
        self._topic_messages[topic] = msg

    def get_subscriptions(self) -> dict[str, int]:
        return self._subscriptions.copy()

    def is_subscribed(self, topic: str) -> bool:
        return topic in self._subscriptions
