from __future__ import annotations

import asyncio
import contextlib
import ssl
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt
import structlog
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCodes as ReasonCode

from fastapi_mqtt_gateway.core.config import Settings

logger = structlog.get_logger()


class MQTTClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: mqtt.Client | None = None
        self._connected = asyncio.Event()
        self._message_queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self._message_callbacks: list[Callable[[str, bytes], None]] = []
        self._loop_task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: ReasonCode | dict[Any, Any],
        reason_code: ReasonCode | int,
        properties: Properties | None,
    ) -> None:
        if reason_code == 0:
            logger.info("MQTT connected", broker=self.settings.mqtt_broker_host)
            self._connected.set()
        else:
            logger.error("MQTT connection failed", reason_code=reason_code)
            self._connected.clear()

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: ReasonCode | dict[Any, Any],
        reason_code: ReasonCode | int,
        properties: Properties | None,
    ) -> None:
        logger.warning("MQTT disconnected", reason_code=reason_code)
        self._connected.clear()

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        payload = msg.payload
        logger.debug("MQTT message received", topic=topic, qos=msg.qos, retain=msg.retain)
        try:
            self._message_queue.put_nowait((topic, payload))
        except asyncio.QueueFull:
            logger.warning("MQTT message queue full, dropping message", topic=topic)
        for callback in self._message_callbacks:
            try:
                callback(topic, payload)
            except Exception as e:
                logger.error("MQTT message callback error", error=str(e), topic=topic)

    def _on_subscribe(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_codes: list[ReasonCode],
        properties: Properties,
    ) -> None:
        logger.debug("MQTT subscribed", mid=mid, reason_codes=reason_codes)

    def _on_log(self, client: mqtt.Client, userdata: Any, level: int, buf: str) -> None:
        logger.debug("MQTT log", level=level, message=buf)

    def add_message_callback(self, callback: Callable[[str, bytes], None]) -> None:
        self._message_callbacks.append(callback)

    def remove_message_callback(self, callback: Callable[[str, bytes], None]) -> None:
        if callback in self._message_callbacks:
            self._message_callbacks.remove(callback)

    async def connect(self) -> None:
        if self._client and self._client.is_connected():
            logger.warning("MQTT client already connected")
            return

        self._client = mqtt.Client(
            client_id=self.settings.mqtt_client_id,
            clean_session=self.settings.mqtt_clean_session,
            protocol=mqtt.MQTTv5,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.on_subscribe = self._on_subscribe
        self._client.on_log = self._on_log

        if self.settings.mqtt_username:
            self._client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)

        if self.settings.mqtt_use_tls:
            self._client.tls_set(
                ca_certs=self.settings.mqtt_ca_certs or None,
                certfile=self.settings.mqtt_certfile or None,
                keyfile=self.settings.mqtt_keyfile or None,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS,
            )

        try:
            self._client.connect_async(
                self.settings.mqtt_broker_host,
                self.settings.mqtt_broker_port,
                keepalive=self.settings.mqtt_keepalive,
            )
            self._client.loop_start()

            await asyncio.wait_for(self._connected.wait(), timeout=10.0)
            logger.info("MQTT connection established")
        except TimeoutError:
            logger.error("MQTT connection timeout")
            raise
        except Exception as e:
            logger.error("MQTT connection error", error=str(e))
            raise

    async def disconnect(self) -> None:
        self._shutdown.set()
        if self._loop_task:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task

        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected.clear()
            logger.info("MQTT disconnected")

    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected() and self._connected.is_set()

    async def subscribe(self, topic: str, qos: int = 0) -> None:
        if not self.is_connected():
            raise RuntimeError("MQTT client not connected")
        logger.info("Subscribing to topic", topic=topic, qos=qos)
        assert self._client is not None  # guaranteed by is_connected()
        self._client.subscribe(topic, qos=qos)

    async def unsubscribe(self, topic: str) -> None:
        if not self.is_connected():
            raise RuntimeError("MQTT client not connected")
        logger.info("Unsubscribing from topic", topic=topic)
        assert self._client is not None  # guaranteed by is_connected()
        self._client.unsubscribe(topic)

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        if not self.is_connected():
            raise RuntimeError("MQTT client not connected")
        if isinstance(payload, str):
            payload = payload.encode()
        logger.debug("Publishing message", topic=topic, qos=qos, retain=retain)
        assert self._client is not None  # guaranteed by is_connected()
        self._client.publish(topic, payload, qos=qos, retain=retain)

    async def get_message(self) -> tuple[str, bytes]:
        return await self._message_queue.get()

    def message_queue_empty(self) -> bool:
        return self._message_queue.empty()
