"""MQTT topic ACLs shared by REST and WebSocket consumers."""

from fastapi import HTTPException
from paho.mqtt.client import topic_matches_sub

from fastapi_mqtt_gateway.core.config import Settings


def validate_topic(topic: str, *, subscription: bool = False) -> None:
    try:
        valid = bool(topic) and len(topic.encode("utf-8")) <= 65535 and "\x00" not in topic
    except UnicodeError:
        valid = False
    levels = topic.split("/")
    if subscription:
        valid = valid and all(
            ("+" not in level or level == "+")
            and ("#" not in level or (level == "#" and index == len(levels) - 1))
            for index, level in enumerate(levels)
        )
        valid = valid and not topic.startswith("$share/")
    else:
        valid = valid and "+" not in topic and "#" not in topic
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid MQTT topic or filter")


def _system_mismatch(left: str, right: str) -> bool:
    return (left.startswith("$") and right[0] in "+#") or (
        right.startswith("$") and left[0] in "+#"
    )


def filter_contains(allowed: str, requested: str) -> bool:
    """Require the entire requested filter to fit one allow rule."""
    if _system_mismatch(allowed, requested):
        return False
    a, r = allowed.split("/"), requested.split("/")
    for index, level in enumerate(a):
        if level == "#":
            return True
        if index >= len(r) or r[index] == "#":
            return False
        if level != "+" and level != r[index]:
            return False
    return len(a) == len(r)


def filters_overlap(left: str, right: str) -> bool:
    """Whether two MQTT filters can match at least one common topic."""
    if _system_mismatch(left, right):
        return False
    a, b = left.split("/"), right.split("/")
    for x, y in zip(a, b, strict=False):
        if "#" in (x, y):
            return True
        if x != "+" and y != "+" and x != y:
            return False
    remaining = a[len(b) :] if len(a) > len(b) else b[len(a) :]
    return not remaining or remaining == ["#"]


def authorize_topic(topic: str, settings: Settings, *, subscription: bool = False) -> None:
    validate_topic(topic, subscription=subscription)
    if subscription:
        allowed = any(filter_contains(rule, topic) for rule in settings.allowed_topic_patterns)
        blocked = any(filters_overlap(rule, topic) for rule in settings.blocked_topic_patterns)
    else:
        allowed = any(topic_matches_sub(rule, topic) for rule in settings.allowed_topic_patterns)
        blocked = any(topic_matches_sub(rule, topic) for rule in settings.blocked_topic_patterns)
    if not allowed or blocked:
        raise HTTPException(status_code=403, detail="Topic not allowed")
