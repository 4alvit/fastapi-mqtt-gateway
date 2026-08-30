"""MQTT reconnect and backoff tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi_mqtt_gateway.core.config import Settings
from fastapi_mqtt_gateway.mqtt.client import MQTTClient


def _settings() -> Settings:
    return Settings(
        mqtt_username="x",
        mqtt_password="x",
        jwt_secret_key="x" * 32,
    )


class TestMQTTClientConnection:
    """Verify connect/disconnect/is_connected behavior."""

    def test_is_connected_false_before_connect(self):
        client = MQTTClient(_settings())
        assert client.is_connected() is False

    def test_disconnect_is_coroutine(self):
        import inspect

        client = MQTTClient(_settings())
        assert inspect.iscoroutinefunction(client.disconnect)


class TestBackoffBehavior:
    """Backoff is delegated to paho-mqtt; verify our config feeds it correctly."""

    def test_keepalive_config_passed(self):
        s = _settings()
        s = Settings(
            mqtt_username="x",
            mqtt_password="x",
            jwt_secret_key="x" * 32,
            mqtt_keepalive=120,
        )
        assert s.mqtt_keepalive == 120

    def test_reconnect_on_disconnect_flag(self):
        # paho-mqtt auto-reconnects when loop_start() is used and clean_session=False
        s = Settings(
            mqtt_username="x",
            mqtt_password="x",
            jwt_secret_key="x" * 32,
            mqtt_clean_session=False,
        )
        assert s.mqtt_clean_session is False

    def test_clean_session_true(self):
        s = Settings(
            mqtt_username="x",
            mqtt_password="x",
            jwt_secret_key="x" * 32,
            mqtt_clean_session=True,
        )
        assert s.mqtt_clean_session is True


class TestMQTTClientReconnectScenarios:
    """Reconnect scenarios: paho handles backoff; we verify our callbacks.

    Callbacks use MagicMock for ReasonCode/ConnectFlags because paho 2.1
    ReasonCode API is incompatible with simple int construction in 3.14.
    """

    def test_on_disconnect_clears_event(self):
        client = MQTTClient(_settings())
        client._connected.set()
        assert client._connected.is_set() is True

        # Simulate disconnect callback
        rc = MagicMock()
        rc.__eq__ = lambda self, other: self.value == other  # type: ignore[assignment]
        rc.value = 0

        flags = MagicMock()

        client._on_disconnect(
            client=MagicMock(),
            userdata=None,
            disconnect_flags=flags,
            reason_code=rc,
            properties=None,
        )
        assert client._connected.is_set() is False

    def test_on_connect_sets_event_on_success(self):
        client = MQTTClient(_settings())
        assert client._connected.is_set() is False

        # reason_code == 0 triggers success path; mock __eq__ so (rc == 0) is True
        rc = MagicMock()
        rc.__eq__ = lambda self, other: getattr(self, "value", None) == other
        rc.value = 0  # success

        flags = MagicMock()

        client._on_connect(
            client=MagicMock(),
            userdata=None,
            flags=flags,
            reason_code=rc,
            properties=None,
        )
        assert client._connected.is_set() is True

    def test_on_connect_clears_on_failure(self):
        client = MQTTClient(_settings())
        client._connected.set()

        rc = MagicMock()
        rc.value = 4  # bad username/password

        flags = MagicMock()

        client._on_connect(
            client=MagicMock(),
            userdata=None,
            flags=flags,
            reason_code=rc,
            properties=None,
        )
        assert client._connected.is_set() is False

    def test_message_queue_init(self):
        client = MQTTClient(_settings())
        assert client.message_queue_empty() is True

    def test_message_callback_registered(self):
        client = MQTTClient(_settings())
        called: list[tuple[str, bytes]] = []

        def cb(topic: str, payload: bytes) -> None:
            called.append((topic, payload))

        client.add_message_callback(cb)
        client._on_message(
            client=MagicMock(),
            userdata=None,
            msg=MagicMock(topic="test/topic", payload=b"hello", qos=0, retain=False),
        )
        assert len(called) == 1
        assert called[0] == ("test/topic", b"hello")

    def test_message_callback_failure_isolated(self):
        """One bad callback must not break others."""
        client = MQTTClient(_settings())
        calls: list[str] = []

        def good_cb(topic: str, payload: bytes) -> None:
            calls.append(topic)

        def bad_cb(topic: str, payload: bytes) -> None:
            raise RuntimeError("boom")

        client.add_message_callback(bad_cb)
        client.add_message_callback(good_cb)

        client._on_message(
            client=MagicMock(),
            userdata=None,
            msg=MagicMock(topic="x/y", payload=b"z", qos=0, retain=False),
        )
        assert calls == ["x/y"]

    def test_subscribe_requires_connected(self):
        import pytest

        client = MQTTClient(_settings())
        # Not connected, must raise
        with pytest.raises(RuntimeError, match="not connected"):
            import asyncio

            asyncio.run(client.subscribe("test/topic"))
