# Changelog

## [0.1.6] - 2026-10-14

### Changed

- Validate each public release candidate with bounded, network-isolated fuzz campaigns against its exact source revision.
- Require positive engine execution counts, instrumented production code and validated seed corpora; retain source-bound reports and diagnostic evidence.
- Run deterministic seed and configuration checks in ordinary CI without starting mutation campaigns on betas.

### Upgrade

Use the existing installation procedure, preserve local configuration and keep the previous accepted package for rollback. This maintenance release updates release validation and does not change the application configuration format. Deployment and applicable device or integration acceptance remain separate from publication.

### Security

The release gate rejects absent or invalid fuzz evidence and unsafe seed inputs. No application vulnerability or new target-device acceptance is claimed by these tooling changes.

## [Unreleased]

### Security

- Close the owned TLS socket on a fatal native handshake error while retaining
  sockets when a nonblocking handshake requests another read or write.

### Changed

- Document project governance, conduct, roadmap and OpenSSF assessment evidence.
- Treat unexpected Python test warnings as failures.


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
