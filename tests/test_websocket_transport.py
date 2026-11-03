"""Exercise the container's installed transport without a broker or external service."""

from __future__ import annotations

import asyncio
import io
import json
import socket
from collections.abc import AsyncIterator, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from uvicorn.config import WSProtocolType
from uvicorn.protocols.websockets.wsproto_impl import WSProtocol
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


@asynccontextmanager
async def running_server(
    app: FastAPI, *, max_size: int = 16 * 1024 * 1024
) -> AsyncIterator[tuple[str, uvicorn.Server]]:
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    command = json.loads(
        next(line[4:] for line in dockerfile.read_text().splitlines() if line.startswith("CMD "))
    )
    protocol = command[command.index("--ws") + 1]
    assert protocol == "wsproto", "Production startup must select the reviewed transport"
    compression = command[command.index("--ws-per-message-deflate") + 1]
    assert compression == "false"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        address = f"ws://127.0.0.1:{listener.getsockname()[1]}/ws"
        config = uvicorn.Config(
            app,
            lifespan="off",
            log_level="error",
            ws=cast(WSProtocolType, protocol),
            ws_max_size=max_size,
            ws_per_message_deflate=False,
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(3):
                while not server.started:
                    if task.done():
                        await task
                        raise AssertionError("Server exited before startup")
                    await asyncio.sleep(0.01)
            yield address, server
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("fragment_count", [1, 4097])
async def test_fragmented_browser_auth_has_no_empty_fragment_accumulation(
    app: FastAPI, valid_token: str, fragment_count: int
) -> None:
    async with (
        asyncio.timeout(12),
        running_server(app) as (uri, server),
        connect(uri + "?topics=devices/%23", ping_interval=None) as websocket,
    ):
        assert websocket.protocol.extensions == []
        fragments_sent = asyncio.Event()
        finish_message = asyncio.Event()

        async def fragments() -> AsyncIterator[str]:
            for _ in range(fragment_count):
                yield ""
            fragments_sent.set()
            await finish_message.wait()
            yield json.dumps({"type": "auth", "token": valid_token})

        sender = asyncio.create_task(websocket.send(fragments()))
        try:
            await asyncio.wait_for(fragments_sent.wait(), 2)
            # A pong confirms preceding fragments traversed the actual socket/parser.
            pong = await websocket.ping()
            await asyncio.wait_for(pong, 2)
            assert len(server.server_state.connections) == 1
            protocol = next(iter(server.server_state.connections))
            assert isinstance(protocol, WSProtocol)
            assert protocol.buffer.length == 0
            assert isinstance(protocol.buffer.value, io.StringIO)
            assert protocol.buffer.value.getvalue() == ""
            finish_message.set()
            await asyncio.wait_for(sender, 2)
            ready = json.loads(await asyncio.wait_for(websocket.recv(), 2))
            assert ready == {"type": "ready", "topics": ["devices/#"]}
        finally:
            finish_message.set()
            if not sender.done():
                sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)


@pytest.mark.parametrize("payload", [b"x" * 65, "\u20ac" * 22])
async def test_fragmented_messages_enforce_byte_limit(payload: bytes | str) -> None:
    app = FastAPI()
    received: list[MutableMapping[str, object]] = []

    @app.websocket("/ws")
    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        received.append(await websocket.receive())

    async with (
        asyncio.timeout(10),
        running_server(app, max_size=64) as (uri, _),
        connect(uri, ping_interval=None) as websocket,
    ):
        with pytest.raises(ConnectionClosed) as closed:
            await websocket.send([payload[:10], payload[10:]])
            await asyncio.wait_for(websocket.recv(), 2)
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 1009
    assert len(received) == 1 and received[0]["type"] == "websocket.disconnect"
    assert received[0]["code"] == 1009
