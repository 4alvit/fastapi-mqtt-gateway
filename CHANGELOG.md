# Changelog

## [0.1.5]

### Security

API authentication now uses a salted PBKDF2-HMAC-SHA256 verifier with 600,000
iterations and a random 128-bit salt. The server no longer requires a plaintext
API password in its environment. Password verification runs off the event loop
with two concurrent KDF operations allowed, preserving the existing login rate
limit. Malformed verifiers and unsupported work factors fail closed.

### Upgrade

Before starting this version, run
`python -m fastapi_mqtt_gateway.password_hash` in a private terminal, set the
printed verifier as `API_PASSWORD_HASH`, and remove `API_PASSWORD` from the
server's environment or secret store. Keep the actual password in the API client
or password manager. The login form, username and JWT interface remain the same.
An installation with only the old plaintext setting will fail startup until
migrated. Keep HTTPS at the ingress; hashing stored credentials does not encrypt
login requests in transit. No live deployment or secret rotation is automatic.

Contribution, vulnerability reporting and deployment-boundary documentation now
include an explicit OpenSSF evidence index. This does not assert an awarded badge.
