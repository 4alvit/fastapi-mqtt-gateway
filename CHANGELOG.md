# Changelog

## [0.1.9] - 2026-10-22

### Fixed

- Connect topic statistics to MQTT packet delivery throughout the application lifespan, and remove the handler before disconnecting the broker client.
- Preserve received QoS, retain flags and wall-clock timestamps; bound the exact-topic cache by `MQTT_QUEUE_SIZE` and enforce existing topic/payload limits.
- Preserve received QoS and retain flags in authenticated WebSocket data frames, including when a REST consumer shares a higher-QoS subscription. Remove the packet callback on disconnect, setup/send failure and cancellation.

### Changed

- Update the locked Ruff development tool to 0.17.1 and refresh the pinned CodeQL actions.
- Refresh certifi to 2026.10.19 and virtualenv to 21.14.9, including its required packaging 26.3 dependency, to pass the current dependency security gate.

### Upgrade

Use the existing update procedure, preserve local configuration and retain the previous immutable package for rollback. No configuration migration is required. Topic statistics count received packets since their current cache entry was created and reset after eviction or restart; wildcard subscriptions do not aggregate counters.

### Security

The dependency refresh addresses the certifi CA-bundle advisory GHSA-6q48-6c7w-9gw2 and the reported virtualenv activation-script vulnerabilities. Required dependency, source and release checks remain enabled. The topic cache applies the existing ACL and payload-size limits before retaining data and cannot grow beyond `MQTT_QUEUE_SIZE` entries. No device acceptance or live broker validation is claimed.

## [0.1.8] - 2026-10-18

### Changed

- Update the development pre-commit tool from 4.6.2 to 4.7.0; application dependencies are unchanged.

### Upgrade

This is a maintenance release with no configuration or data migration. Preserve installation-specific configuration and retain the previous package for rollback.

### Security

Required CI, dependency checks and source-bound RC qualification remain enabled. No additional runtime security fix is claimed.

## [0.1.7] - 2026-10-17

### Changed

- Validate the pinned Scorecard 5.6 report with the current CI contract.
- Document where RC fuzz safeguards execute and add illustrated process and result guides.

### Upgrade

This release updates CI validation and operator documentation; application configuration and runtime behavior are unchanged from 0.1.6. Preserve local configuration and retain the previous package for rollback.

### Security

The Scorecard contract change improves validation of security evidence. It does not claim a new application vulnerability fix or additional device acceptance.

## [0.1.6] - 2026-10-14

### Changed

- Validate each public release candidate with bounded, network-isolated fuzz campaigns against its exact source revision.
- Require positive engine execution counts, instrumented production code and validated seed corpora; retain source-bound reports and diagnostic evidence.
- Run deterministic seed and configuration checks in ordinary CI without starting mutation campaigns on betas.

### Upgrade

Use the existing installation procedure, preserve local configuration and keep the previous accepted package for rollback. This maintenance release updates release validation and does not change the application configuration format. Deployment and applicable device or integration acceptance remain separate from publication.

### Security

RC publication rejects absent or invalid source-bound fuzz reports. The fuzz runner bounds corpus paths, file types and size. No application vulnerability or new target-device acceptance is claimed by these tooling changes.

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
