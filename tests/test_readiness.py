"""Readiness follows broker connectivity while liveness stays available."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient


def test_broker_disconnect_only_fails_readiness(
    client: TestClient, mock_mqtt_client: MagicMock
) -> None:
    assert client.get("/ready").status_code == 200
    mock_mqtt_client.is_connected.return_value = False
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "MQTT broker is disconnected"}
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["mqtt_connected"] is False
    mock_mqtt_client.is_connected.return_value = True
    assert client.get("/ready").json()["mqtt_connected"] is True
