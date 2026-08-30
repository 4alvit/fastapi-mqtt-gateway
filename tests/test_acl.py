"""Topic validation tests: Pydantic model constraints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fastapi_mqtt_gateway.models import PublishRequest, SubscribeRequest


class TestTopicValidation:
    """Topic field constraints enforced at Pydantic layer."""

    def test_empty_topic_rejected(self):
        with pytest.raises(ValidationError) as exc:
            PublishRequest(topic="", payload="x")
        assert "topic" in str(exc.value)

    def test_topic_too_long_rejected(self):
        with pytest.raises(ValidationError):
            PublishRequest(topic="a" * 65536, payload="x")

    def test_valid_topic_accepted(self):
        req = PublishRequest(topic="devices/sensor1/temp", payload="22.5")
        assert req.topic == "devices/sensor1/temp"

    def test_subscribe_empty_topic_rejected(self):
        with pytest.raises(ValidationError):
            SubscribeRequest(topic="", qos=0)

    def test_qos_bounds(self):
        for qos in [0, 1, 2]:
            req = PublishRequest(topic="x", payload="y", qos=qos)
            assert req.qos == qos
        with pytest.raises(ValidationError):
            PublishRequest(topic="x", payload="y", qos=3)
        with pytest.raises(ValidationError):
            PublishRequest(topic="x", payload="y", qos=-1)
