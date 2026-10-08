"""Per-client MQTT TLS context with Paho-compatible trust configuration."""

import ssl

from fastapi_mqtt_gateway.tls_policy import enforce_peer_key_policy


def mqtt_context(
    *, ca_certs: str | None, certfile: str | None, keyfile: str | None
) -> ssl.SSLContext:
    """Preserve CA/client-certificate loading before enforcing peer key minima."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if certfile is not None:
        context.load_cert_chain(certfile, keyfile)
    if ca_certs is not None:
        context.load_verify_locations(ca_certs)
    else:
        context.load_default_certs()
    return enforce_peer_key_policy(context)
