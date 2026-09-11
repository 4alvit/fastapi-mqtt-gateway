"""Exercise authentication, isolation and cleanup through the ASGI interface."""

import asyncio
import threading
import time
from unittest.mock import Mock

import jwt
import pytest
from starlette.websockets import WebSocketDisconnect

from fastapi_mqtt_gateway.core.auth import verify_token
from fastapi_mqtt_gateway.core.topics import authorize_topic
from fastapi_mqtt_gateway.mqtt.client import MQTTClient


def test_login_rejects_query_string_credentials(client):
    response = client.post("/auth/token?username=testuser&password=test-api-password-1234")
    assert response.status_code == 422


def test_broker_credentials_do_not_grant_api_access(client, settings):
    response = client.post(
        "/auth/token", data={"username": settings.mqtt_username, "password": settings.mqtt_password}
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "testuser"},
        {"exp": 9999999999},
        {"sub": "testuser", "exp": 1},
        {"sub": "another-user", "exp": 9999999999},
        {"sub": "testuser", "exp": 9999999999, "scopes": "admin"},
    ],
)
def test_invalid_claims_fail_closed(settings, claims):
    token = jwt.encode(claims, settings.jwt_secret_key, algorithm="HS256")
    assert verify_token(token) is None


def test_websocket_rejects_invalid_bearer_before_accept(client, mock_mqtt_client):
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws?topics=devices/%23", headers={"Authorization": "Bearer bad"}),
    ):
        pass
    assert exc.value.code == 1008
    mock_mqtt_client.subscribe.assert_not_called()


def test_browser_must_authenticate_before_receiving_data(client, mock_mqtt_client):
    with client.websocket_connect("/ws?topics=devices/%23") as ws:
        ws.send_json({"type": "auth", "token": "bad"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1008
    mock_mqtt_client.subscribe.assert_not_called()
    mock_mqtt_client.add_message_callback.assert_not_called()


def test_browser_auth_deadline(client, settings, mock_mqtt_client):
    settings.websocket_auth_timeout = 0.02
    with (
        client.websocket_connect("/ws?topics=devices/%23") as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        ws.receive_json()
    assert exc.value.code == 1008
    mock_mqtt_client.subscribe.assert_not_called()


def test_browser_auth_frame_and_topic_isolation(client, valid_token, mock_mqtt_client):
    async def subscribe(topic, qos):
        mock_mqtt_client.emit("private/unrequested", b"must not escape")
        mock_mqtt_client.emit("devices/temperature", b"23")

    mock_mqtt_client.subscribe.side_effect = subscribe
    with client.websocket_connect("/ws?topics=devices/%23") as ws:
        ws.send_json({"type": "auth", "token": valid_token})
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["topic"] == "devices/temperature"
    mock_mqtt_client.unsubscribe.assert_awaited_once_with("devices/#")


@pytest.mark.parametrize("topic", ["admin/%23", "sensors/%23", "%23", "devices/invalid%23"])
def test_ws_rejects_forbidden_and_overbroad_filters(client, auth_headers, mock_mqtt_client, topic):
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(f"/ws?topics={topic}", headers=auth_headers),
    ):
        pass
    assert exc.value.code == 1008
    mock_mqtt_client.subscribe.assert_not_called()


def test_stream_ends_when_token_expires(client, settings, mock_mqtt_client):
    token = jwt.encode(
        {"sub": settings.api_username, "exp": time.time() + 1.5},
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    with (
        client.websocket_connect(
            "/ws?topics=devices/%23", headers={"Authorization": f"Bearer {token}"}
        ) as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        assert ws.receive_json()["type"] == "ready"
        ws.receive_json()
    assert exc.value.code == 1008
    mock_mqtt_client.unsubscribe.assert_awaited_once_with("devices/#")


def test_disconnect_preserves_other_websocket_and_rest_owners(
    client, auth_headers, mock_mqtt_client
):
    response = client.post("/mqtt/subscribe", json={"topic": "devices/#"}, headers=auth_headers)
    assert response.status_code == 200
    with client.websocket_connect("/ws?topics=devices/%23", headers=auth_headers) as first:
        assert first.receive_json()["type"] == "ready"
        with client.websocket_connect("/ws?topics=devices/%23", headers=auth_headers) as second:
            assert second.receive_json()["type"] == "ready"
            client.portal.call(mock_mqtt_client.emit, "devices/a", b"first")
            assert first.receive_json()["payload"] == "first"
            assert second.receive_json()["payload"] == "first"
        mock_mqtt_client.unsubscribe.assert_not_called()
        client.portal.call(mock_mqtt_client.emit, "devices/a", b"second")
        assert first.receive_json()["payload"] == "second"
    mock_mqtt_client.unsubscribe.assert_not_called()
    response = client.post("/mqtt/unsubscribe", json={"topic": "devices/#"}, headers=auth_headers)
    assert response.status_code == 200
    mock_mqtt_client.unsubscribe.assert_awaited_once_with("devices/#")


def test_slow_consumer_is_closed_on_queue_overflow(
    client, settings, auth_headers, mock_mqtt_client
):
    settings.websocket_queue_size = 2

    async def subscribe(topic, qos):
        for _ in range(10):
            mock_mqtt_client.emit("devices/a", b"payload")

    mock_mqtt_client.subscribe.side_effect = subscribe
    with (
        client.websocket_connect("/ws?topics=devices/%23", headers=auth_headers) as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        while True:
            ws.receive_json()
    assert exc.value.code == 1013
    mock_mqtt_client.unsubscribe.assert_awaited_once_with("devices/#")


def test_failed_second_subscription_cleans_up_first(client, auth_headers, mock_mqtt_client):
    mock_mqtt_client.subscribe.side_effect = [None, RuntimeError("broker unavailable")]
    with (
        client.websocket_connect("/ws?topics=devices/a,devices/b", headers=auth_headers) as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        ws.receive_json()
    assert exc.value.code == 1011
    mock_mqtt_client.unsubscribe.assert_awaited_once_with("devices/a")
    mock_mqtt_client.remove_message_callback.assert_called_once()


@pytest.mark.parametrize(
    "endpoint, body",
    [
        ("publish", {"topic": "sensors/private/key", "payload": "value"}),
        ("subscribe", {"topic": "sensors/#"}),
        ("retained", {"topic": "admin/password"}),
        ("unsubscribe", {"topic": "admin/#"}),
    ],
)
def test_rest_and_ws_share_acl(client, auth_headers, endpoint, body):
    assert client.post(f"/mqtt/{endpoint}", json=body, headers=auth_headers).status_code == 403


def test_default_hash_filter_does_not_include_system_topics(settings):
    settings.allowed_topic_patterns = ["#"]
    settings.blocked_topic_patterns = ["$SYS/#", "$share/#"]
    authorize_topic("#", settings, subscription=True)


def test_paho_ingress_and_scheduling_are_bounded(settings):
    settings.mqtt_queue_size = 2
    client = MQTTClient(settings)
    loop = Mock()
    client._event_loop = loop
    for i in range(100):
        client._on_message(Mock(), None, Mock(topic="devices/a", payload=str(i).encode()))
    assert len(client._pending_messages) == 2
    loop.call_soon_threadsafe.assert_called_once()
    client._drain_messages()
    assert client._message_queue.qsize() == 2
    assert client._message_queue.get_nowait()[1] == b"98"


def test_oversized_message_never_reaches_callbacks(settings):
    settings.max_message_bytes = 4
    client = MQTTClient(settings)
    callback = Mock()
    client.add_message_callback(callback)
    client._on_message(Mock(), None, Mock(topic="devices/a", payload=b"12345"))
    callback.assert_not_called()
    assert client.message_queue_empty()


async def test_paho_thread_dispatches_callbacks_on_event_loop(settings):
    client = MQTTClient(settings)
    client._event_loop = asyncio.get_running_loop()
    received = asyncio.Event()
    callback_threads = []

    def callback(topic, payload):
        callback_threads.append(threading.get_ident())
        received.set()

    client.add_message_callback(callback)
    worker = threading.Thread(
        target=client._on_message,
        args=(Mock(), None, Mock(topic="devices/a", payload=b"23")),
    )
    worker.start()
    await asyncio.wait_for(received.wait(), timeout=1)
    worker.join(timeout=1)
    assert callback_threads == [threading.get_ident()]


async def test_disconnected_release_is_not_resubscribed(settings):
    client = MQTTClient(settings)
    client._client = Mock()
    client._client.is_connected.return_value = True
    client._client.subscribe.return_value = (0, 1)
    client._connection_changed(True)
    await client.subscribe("devices/a")
    client._connection_changed(False)
    await client.unsubscribe("devices/a")
    client._client.subscribe.reset_mock()
    client._connection_changed(True)
    client._client.subscribe.assert_not_called()
