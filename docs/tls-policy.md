# TLS certificate key policy

Owned TLS contexts retain CA and hostname verification and inspect the actual
verified chain, including its selected trust anchor, before application data.
RSA moduli must contain at least 2048 significant bits; EC keys need at least
224 bits; DSA requires p >= 2048 and q >= 224. Ed25519 and Ed448 are accepted.
Unknown algorithms or runtimes without an accessible verified chain fail closed.
OpenSSL security level 2 alone can accept a 2047-bit RSA modulus.

CPython 3.11 and 3.12 use the private `_sslobj.get_verified_chain` interface;
newer CPython versions may expose the public equivalent. This runtime contract
is tested rather than inferred from the Python version. Alternative Python
implementations are not implicitly supported.

The public-key decoder uses cryptography except on Intel macOS, where the
native Security framework reads key metadata without changing trust decisions.
The Intel backend accepts RSA and EC only. No system trust store is modified.

The TLS policy and Darwin metadata decoder are adapted from the MIT-licensed
victron-venus/inverter-dashboard implementation, copyright 2026 victron-venus.
The project MIT license also applies to these adaptations. This policy does
not establish the strength of inbound TLS terminators or unrelated transports.

## MQTT broker TLS

When MQTT_USE_TLS is enabled, each Paho client receives its own verified TLS
context. The configured MQTT_CA_CERTS file or the normal default trust roots
remain in use. MQTT_CERTFILE and MQTT_KEYFILE still load the client identity
for mutual TLS, and hostname verification remains mandatory. Every verified
broker-chain key, including an omitted trust anchor, is inspected before the
MQTT CONNECT packet can expose the username or password.

Replace broker/CA keys below the stated minima before upgrading. Unsupported
verified-chain runtimes fail closed. This does not enable TLS for the existing
MQTT_USE_TLS=false local deployment, inspect inbound reverse-proxy termination,
or establish a complete policy for operator-supplied client identity keys.
It is a scoped broker peer-key correction, not a project-wide attestation.
