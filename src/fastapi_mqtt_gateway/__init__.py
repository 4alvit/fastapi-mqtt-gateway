"""FastAPI MQTT Gateway - Production-ready REST/WebSocket to MQTT bridge."""

from fastapi_mqtt_gateway._version import __version__
from fastapi_mqtt_gateway.core.config import Settings, get_settings

__all__ = ["Settings", "get_settings", "__version__"]
