from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from fastapi_mqtt_gateway.api import limiter
from fastapi_mqtt_gateway.api import router as api_router
from fastapi_mqtt_gateway.core.config import get_settings
from fastapi_mqtt_gateway.mqtt.client import MQTTClient
from fastapi_mqtt_gateway.services.mqtt_service import MQTTService

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    logger.info("Starting application", version=settings.app_version)

    mqtt_client = MQTTClient(settings)
    mqtt_service = MQTTService(mqtt_client, settings)
    try:
        mqtt_client.add_packet_callback(mqtt_service.handle_message)
        await mqtt_client.connect()
        app.state.mqtt_client = mqtt_client
        app.state.mqtt_service = mqtt_service
        yield
    finally:
        logger.info("Shutting down application")
        mqtt_client.remove_packet_callback(mqtt_service.handle_message)
        await mqtt_client.disconnect()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    limiter.enabled = settings.rate_limit_enabled
    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded,
        _rate_limit_exceeded_handler,  # type: ignore[arg-type]  # slowapi predates Starlette union
    )

    app.include_router(api_router)

    return app


app = create_app()
