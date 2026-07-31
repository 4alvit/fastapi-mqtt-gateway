"""FastAPI MQTT Gateway - Production-ready REST/WebSocket to MQTT bridge."""

from fastapi_mqtt_gateway.core.config import Settings, get_settings

__version__ = "0.1.0"
__all__ = ["Settings", "get_settings"]
