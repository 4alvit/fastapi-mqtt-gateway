"""Actual application/Paho TLS and CONNECT exchanges with disposable brokers."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import paho.mqtt.client as paho
import pytest

from fastapi_mqtt_gateway.core.config import Settings
from fastapi_mqtt_gateway.mqtt.client import MQTTClient

from .test_tls_policy import CHAIN_CASES, calibrate, chains, clean_environment

__all__ = ["chains", "clean_environment"]


@contextlib.contextmanager
def broker(
    chain: tuple[Path, Path, Path], version: ssl.TLSVersion, *, client_auth: bool = False
) -> Iterator[tuple[int, dict[str, Any]]]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = version
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_cert_chain(chain[0], chain[1])
    if client_auth:
        context.load_verify_locations(chain[2])
        context.verify_mode = ssl.CERT_REQUIRED
    observed: dict[str, Any] = {"bytes": b""}
    stop = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def serve() -> None:
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    with context.wrap_socket(raw, server_side=True) as connection:
                        observed["tls"] = connection.version()
                        if client_auth:
                            observed["client_certificate"] = connection.getpeercert(
                                binary_form=True
                            )
                        header = connection.recv(1)
                        if not header:
                            return
                        observed["bytes"] = header
                        assert header == b"\x10"
                        length = 0
                        for shift in range(0, 28, 7):
                            value = connection.recv(1)
                            assert len(value) == 1
                            observed["bytes"] += value
                            length += (value[0] & 127) << shift
                            if value[0] < 128:
                                break
                        else:
                            raise AssertionError("Invalid CONNECT length")
                        assert length < 8192
                        data = b""
                        while len(data) < length:
                            part = connection.recv(length - len(data))
                            assert part
                            data += part
                            observed["bytes"] += part
                        assert data[6] == 5
                        connection.sendall(b"\x20\x03\x00\x00\x00")
                        connection.settimeout(0.1)
                        while not stop.is_set():
                            try:
                                if not connection.recv(4096):
                                    break
                            except TimeoutError:
                                continue
            except (ssl.SSLError, ConnectionResetError) as error:
                observed["tls_error"] = str(error)
            except Exception as error:
                observed["unexpected"] = repr(error)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], observed
        finally:
            stop.set()
            thread.join(6)
            assert not thread.is_alive()
            assert "unexpected" not in observed, observed


async def connect_once(settings: Settings, attempts: list[Exception]) -> bool:
    client = MQTTClient(settings)
    task = asyncio.create_task(client.connect())
    try:
        for _ in range(600):
            if task.done():
                task.result()
                return True
            if attempts:
                return False
            await asyncio.sleep(0.01)
        raise AssertionError("No native TLS outcome before deadline")
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await client.disconnect()


@pytest.mark.parametrize("case", [*CHAIN_CASES, "untrusted", "wrong-host"])
@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
async def test_actual_mqtt_rejects_before_connect_credentials(
    chains: dict[str, tuple[Path, Path, Path]],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    version: ssl.TLSVersion,
) -> None:
    chain = chains.get(case, chains["strong"])
    calibrate(chain, version)
    ca = chains["strong-ec"][2] if case == "untrusted" else chain[2]
    failures: list[Exception] = []
    original = paho.Client._ssl_wrap_socket

    def observed_handshake(client: paho.Client, raw: socket.socket) -> ssl.SSLSocket:
        try:
            return original(client, raw)
        except ssl.SSLError as error:
            failures.append(error)
            raise

    monkeypatch.setattr(paho.Client, "_ssl_wrap_socket", observed_handshake)
    with broker(chain, version) as (port, observed):
        settings = Settings.model_construct(
            mqtt_broker_host="127.0.0.1" if case == "wrong-host" else "localhost",
            mqtt_broker_port=port,
            mqtt_use_tls=True,
            mqtt_ca_certs=str(ca),
            mqtt_username="synthetic-user",
            mqtt_password="synthetic-password",
        )
        accepted = await connect_once(settings, failures)
    assert accepted == case.startswith("strong")
    assert bool(observed["bytes"]) == accepted
    assert (b"synthetic-password" in observed["bytes"]) == accepted
    assert bool(failures) != accepted
    if accepted:
        assert (
            observed["tls"]
            == {ssl.TLSVersion.TLSv1_2: "TLSv1.2", ssl.TLSVersion.TLSv1_3: "TLSv1.3"}[version]
        )


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
@pytest.mark.parametrize("explicit_ca", [False, True])
async def test_client_certificate_and_default_ca_loading_preserved(
    chains: dict[str, tuple[Path, Path, Path]],
    monkeypatch: pytest.MonkeyPatch,
    version: ssl.TLSVersion,
    explicit_ca: bool,
) -> None:
    chain = chains["strong"]
    monkeypatch.setenv("SSL_CERT_FILE", str(chain[2]))
    with broker(chain, version, client_auth=True) as (port, observed):
        settings = Settings.model_construct(
            mqtt_broker_host="localhost",
            mqtt_broker_port=port,
            mqtt_use_tls=True,
            mqtt_ca_certs=str(chain[2]) if explicit_ca else "",
            mqtt_certfile=str(chain[0]),
            mqtt_keyfile=str(chain[1]),
            mqtt_username="synthetic-user",
            mqtt_password="synthetic-password",
        )
        assert await connect_once(settings, [])
    expected = ssl.PEM_cert_to_DER_cert(chain[0].read_text())
    assert observed["client_certificate"] == expected
    assert (
        observed["tls"]
        == {ssl.TLSVersion.TLSv1_2: "TLSv1.2", ssl.TLSVersion.TLSv1_3: "TLSv1.3"}[version]
    )
    assert b"synthetic-password" in observed["bytes"]
