import asyncio
import contextlib
import hmac
import time
from typing import Annotated
from uuid import uuid4

import anyio
from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from paho.mqtt.client import topic_matches_sub
from slowapi import Limiter
from slowapi.util import get_remote_address

from fastapi_mqtt_gateway.core.auth import (
    User,
    create_access_token,
    get_current_user,
    verify_token,
)
from fastapi_mqtt_gateway.core.config import get_settings
from fastapi_mqtt_gateway.core.topics import authorize_topic
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


def get_mqtt_client(request: Request) -> MQTTClient:
    client: MQTTClient = request.app.state.mqtt_client
    return client


def get_mqtt_service(request: Request) -> MQTTService:
    service: MQTTService = request.app.state.mqtt_service
    return service


@router.post("/auth/token", response_model=Token)
@limiter.limit("10/minute")
async def login(
    request: Request,
    username: Annotated[str, Form(min_length=1, max_length=256)],
    password: Annotated[str, Form(min_length=1, max_length=1024)],
) -> Token:
    settings = get_settings()
    username_ok = hmac.compare_digest(username.encode(), settings.api_username.encode())
    password_ok = hmac.compare_digest(password.encode(), settings.api_password.encode())
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    access_token = create_access_token(data={"sub": username})
    expires_in = settings.jwt_access_token_expire_minutes * 60
    return Token(access_token=access_token, expires_in=expires_in)


async def get_user_dep(
    user: Annotated[User, Depends(get_current_user)],
) -> str:
    return user.username


UserDep = Annotated[str, Depends(get_user_dep)]
ServiceDep = Annotated[MQTTService, Depends(get_mqtt_service)]
ClientDep = Annotated[MQTTClient, Depends(get_mqtt_client)]


@router.post("/mqtt/publish", response_model=PublishResponse)
@limiter.limit("100/minute")
async def publish_message(
    request: Request, body: PublishRequest, _user: UserDep, mqtt_service: ServiceDep
) -> PublishResponse:
    return await mqtt_service.publish(body)


@router.post("/mqtt/subscribe", response_model=SubscribeResponse)
@limiter.limit("50/minute")
async def subscribe_topic(
    request: Request, body: SubscribeRequest, _user: UserDep, mqtt_service: ServiceDep
) -> SubscribeResponse:
    return await mqtt_service.subscribe(body)


@router.post("/mqtt/unsubscribe", response_model=UnsubscribeResponse)
@limiter.limit("50/minute")
async def unsubscribe_topic(
    request: Request, body: UnsubscribeRequest, _user: UserDep, mqtt_service: ServiceDep
) -> UnsubscribeResponse:
    return await mqtt_service.unsubscribe(body)


@router.post("/mqtt/retained", response_model=RetainedQueryResponse)
@limiter.limit("20/minute")
async def query_retained_message(
    request: Request, body: RetainedQueryRequest, _user: UserDep, mqtt_service: ServiceDep
) -> RetainedQueryResponse:
    return await mqtt_service.query_retained(body)


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
    settings = get_settings()
    token = ""
    accepted = False
    header = websocket.headers.get("authorization")
    if header is not None:
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            token = ""
    else:
        # Browser WebSocket APIs cannot set Authorization. Accept only an auth
        # frame within a short deadline, with no broker access before validation.
        await websocket.accept()
        accepted = True
        try:
            frame = await asyncio.wait_for(
                websocket.receive_json(), timeout=settings.websocket_auth_timeout
            )
            if isinstance(frame, dict) and frame.get("type") == "auth":
                token = frame.get("token", "")
        except (TimeoutError, ValueError, TypeError, KeyError, WebSocketDisconnect):
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1008, reason="Authentication required")
            return
    claims = verify_token(token) if isinstance(token, str) and len(token) <= 8192 else None
    if not claims:
        await websocket.close(code=1008, reason="Invalid or missing token")
        return

    requested = list(dict.fromkeys(topic.strip() for topic in topics.split(",") if topic.strip()))
    try:
        if not requested or len(requested) > settings.websocket_max_topics:
            raise HTTPException(status_code=400, detail="Invalid number of topics")
        for topic in requested:
            authorize_topic(topic, settings, subscription=True)
    except HTTPException:
        await websocket.close(code=1008, reason="Topic not allowed")
        return

    if not accepted:
        await websocket.accept()
    mqtt_client: MQTTClient = websocket.app.state.mqtt_client
    mqtt_service: MQTTService = websocket.app.state.mqtt_service
    message_queue: asyncio.Queue[MQTTMessage] = asyncio.Queue(maxsize=settings.websocket_queue_size)
    owner = f"websocket:{uuid4()}"
    acquired: list[str] = []
    overflow = asyncio.Event()

    def ws_callback(topic: str, payload: bytes) -> None:
        if not any(topic_matches_sub(pattern, topic) for pattern in requested):
            return
        # MQTTClient dispatches callbacks on the ASGI event loop.
        if len(payload) > settings.max_message_bytes:
            return
        try:
            message_queue.put_nowait(
                MQTTMessage(
                    topic=topic,
                    payload=payload.decode(errors="replace"),
                    qos=0,
                    retain=False,
                    timestamp=time.time(),
                )
            )
        except asyncio.QueueFull:
            overflow.set()

    async def send_messages() -> tuple[int, str]:
        while True:
            remaining = float(claims["exp"]) - time.time()
            if remaining <= 0:
                return 1008, "Token expired"
            try:
                msg = await asyncio.wait_for(message_queue.get(), timeout=min(30, remaining))
                data = msg.model_dump()
            except TimeoutError:
                if time.time() >= float(claims["exp"]):
                    continue
                data = {"type": "ping"}
            try:
                await asyncio.wait_for(
                    websocket.send_json(data),
                    timeout=min(
                        settings.websocket_send_timeout,
                        max(0.001, float(claims["exp"]) - time.time()),
                    ),
                )
            except TimeoutError:
                return 1013, "Client cannot keep up"

    async def receive_disconnect() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

    finished = asyncio.Event()
    close_message: tuple[int, str] | None = None
    mqtt_client.add_message_callback(ws_callback)
    try:
        for topic in requested:
            await mqtt_service.acquire_subscription(topic, 0, owner)
            acquired.append(topic)
        await asyncio.wait_for(
            websocket.send_json({"type": "ready", "topics": requested}),
            timeout=settings.websocket_send_timeout,
        )
        async with anyio.create_task_group() as group:

            async def run_sender() -> None:
                nonlocal close_message
                try:
                    close_message = await send_messages()
                except (WebSocketDisconnect, RuntimeError, TimeoutError):
                    close_message = (1011, "Stream unavailable")
                finally:
                    finished.set()

            async def run_receiver() -> None:
                try:
                    await receive_disconnect()
                finally:
                    finished.set()

            async def run_overflow() -> None:
                nonlocal close_message
                await overflow.wait()
                close_message = (1013, "Client cannot keep up")
                finished.set()

            group.start_soon(run_sender)
            group.start_soon(run_receiver)
            group.start_soon(run_overflow)
            await finished.wait()
            group.cancel_scope.cancel()
        # Only the owner task sends a close frame, after the sender has stopped.
        # This avoids racing data frames and close frames on a slow connection.
        if close_message is not None:
            await websocket.close(code=close_message[0], reason=close_message[1])
    except (WebSocketDisconnect, RuntimeError, TimeoutError):
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=1011, reason="Stream unavailable")
    finally:
        mqtt_client.remove_message_callback(ws_callback)
        # ASGI servers cancel disconnected requests. Cleanup must still release
        # this connection's subscriptions without touching another owner's.
        with anyio.CancelScope(shield=True):
            for topic in acquired:
                with contextlib.suppress(RuntimeError):
                    await mqtt_service.release_subscription(topic, owner)
