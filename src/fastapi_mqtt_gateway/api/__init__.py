import asyncio
import contextlib
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from slowapi import Limiter
from slowapi.util import get_remote_address

from fastapi_mqtt_gateway.core.auth import create_access_token
from fastapi_mqtt_gateway.core.config import get_settings
from fastapi_mqtt_gateway.models import (
    HealthResponse,
    MQTTMessage,
    PublishRequest,
    PublishResponse,
    RetainedQueryRequest,
    RetainedQueryResponse,
    SubscribeRequest,
    SubscribeResponse,
    Token,
    TopicInfo,
    UnsubscribeRequest,
    UnsubscribeResponse,
)
from fastapi_mqtt_gateway.mqtt.client import MQTTClient
from fastapi_mqtt_gateway.services.mqtt_service import MQTTService

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


def get_mqtt_client() -> MQTTClient:
    from fastapi_mqtt_gateway.main import app

    client: MQTTClient = app.state.mqtt_client
    return client


def get_mqtt_service() -> MQTTService:
    from fastapi_mqtt_gateway.main import app

    service: MQTTService = app.state.mqtt_service
    return service


@router.post("/auth/token", response_model=Token)
@limiter.limit("10/minute")
async def login(request: Request, username: str, password: str) -> Token:
    settings = get_settings()
    if username != settings.mqtt_username or password != settings.mqtt_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    access_token = create_access_token(data={"sub": username})
    expires_in = settings.jwt_access_token_expire_minutes * 60
    return Token(access_token=access_token, expires_in=expires_in)


async def get_user_dep() -> str:
    from fastapi_mqtt_gateway.core.auth import get_current_user as _get_current_user

    user = await _get_current_user()
    return user.username


UserDep = Annotated[str, Depends(get_user_dep)]
ServiceDep = Annotated[MQTTService, Depends(get_mqtt_service)]
ClientDep = Annotated[MQTTClient, Depends(get_mqtt_client)]


@router.post("/mqtt/publish", response_model=PublishResponse)
@limiter.limit("100/minute")
async def publish_message(
    request: PublishRequest, _user: UserDep, mqtt_service: ServiceDep
) -> PublishResponse:
    return await mqtt_service.publish(request)


@router.post("/mqtt/subscribe", response_model=SubscribeResponse)
@limiter.limit("50/minute")
async def subscribe_topic(
    request: SubscribeRequest, _user: UserDep, mqtt_service: ServiceDep
) -> SubscribeResponse:
    return await mqtt_service.subscribe(request)


@router.post("/mqtt/unsubscribe", response_model=UnsubscribeResponse)
@limiter.limit("50/minute")
async def unsubscribe_topic(
    request: UnsubscribeRequest, _user: UserDep, mqtt_service: ServiceDep
) -> UnsubscribeResponse:
    return await mqtt_service.unsubscribe(request)


@router.post("/mqtt/retained", response_model=RetainedQueryResponse)
@limiter.limit("20/minute")
async def query_retained_message(
    request: RetainedQueryRequest, _user: UserDep, mqtt_service: ServiceDep
) -> RetainedQueryResponse:
    return await mqtt_service.query_retained(request)


@router.get("/mqtt/topics", response_model=list[TopicInfo])
@limiter.limit("30/minute")
async def list_topics(
    request: Request, _user: UserDep, mqtt_service: ServiceDep
) -> list[TopicInfo]:
    subscriptions = mqtt_service.get_subscriptions()
    return [await mqtt_service.get_topic_info(topic) for topic in subscriptions]


@router.get("/mqtt/topics/{topic:path}", response_model=TopicInfo)
@limiter.limit("30/minute")
async def get_topic_info(
    request: Request, topic: str, _user: UserDep, mqtt_service: ServiceDep
) -> TopicInfo:
    if not mqtt_service.is_subscribed(topic):
        raise HTTPException(status_code=404, detail="Topic not subscribed")
    return await mqtt_service.get_topic_info(topic)


@router.get("/health", response_model=HealthResponse)
async def health_check(mqtt_client: ClientDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=get_settings().app_version,
        mqtt_connected=mqtt_client.is_connected() if mqtt_client else False,
    )


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    topics: str = Query(default="#", description="Comma-separated topics to subscribe"),
) -> None:
    mqtt_client = get_mqtt_client()
    mqtt_service = get_mqtt_service()

    await websocket.accept()

    message_queue: asyncio.Queue = asyncio.Queue()

    def ws_callback(topic: str, payload: bytes) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            message_queue.put_nowait(
                MQTTMessage(
                    topic=topic,
                    payload=payload.decode() if isinstance(payload, bytes) else payload,
                    qos=0,
                    retain=False,
                    timestamp=asyncio.get_event_loop().time(),
                )
            )

    mqtt_client.add_message_callback(ws_callback)

    try:
        if topics:
            for topic in topics.split(","):
                topic = topic.strip()
                if topic:
                    await mqtt_client.subscribe(topic)

        while True:
            try:
                msg = await asyncio.wait_for(message_queue.get(), timeout=30.0)
                await websocket.send_json(msg.model_dump())
            except TimeoutError:
                await websocket.send_json({"type": "ping"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.close(code=1011, reason=str(e))
    finally:
        mqtt_client.remove_message_callback(ws_callback)
        if topics:
            for topic in topics.split(","):
                topic = topic.strip()
                if topic and mqtt_service and mqtt_service.is_subscribed(topic):
                    await mqtt_service.unsubscribe(UnsubscribeRequest(topic=topic))
