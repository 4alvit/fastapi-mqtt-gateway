# Security assurance and evidence limits

Assessment date: 2026-10-13. Application evidence was collected from source `2b049ff3584429367cbe73cb31e05c963c908bfb`. The threat model assumes a maintained Python runtime, trusted host/configuration, and operators who control deployment credentials. The claim is bounded risk reduction through concrete controls and tests, not a formal security certification.

## Data flow and trust boundaries

Untrusted REST/WebSocket requests pass through the FastAPI API, JWT validation and topic ACLs into the MQTT client. The client uses bounded queues and separates publish acceptance from downstream device action. The API's /health reports process health; /ready also requires broker connectivity. The deployment must provide trusted HTTPS ingress and broker permissions. A permitted publish may still control downstream equipment.

`core/config.py` refuses missing or placeholder API credentials and restricts JWT algorithms to HS256. `core/auth.py` requires expiry and subject and checks the configured username. `core/passwords.py` stores PBKDF2-HMAC-SHA256 verifiers with 600,000 iterations, a 128-bit random salt from `secrets.token_bytes`, and constant-time digest comparison. Password length and verifier work factor are bounded. Rotating the environment-provided signing key invalidates old tokens; rotate API and broker credentials through their own issuers.

TLS-enabled Paho connections use `mqtt/tls.py` and `tls_policy.py`: ordinary chain/hostname validation is followed by exact certificate-key minimum checks. TLS 1.2 is the minimum; RSA keys must be at least 2048 bits. MQTT TLS remains opt-in, and HTTP ingress is deployment-managed: encrypted traffic is not guaranteed by default. Configure both before crossing an untrusted network. See [TLS policy](tls-policy.md).

## Static analysis triage

The application-only Bandit 1.8.6 scan on 2026-10-13 reviewed all severities: one medium and five low findings, with no scanner errors. B104 at the configured `0.0.0.0` listener is intentional container service exposure; do not expose it without the documented authenticated ingress. Two B105 findings are empty WebSocket-token sentinels, not usable credentials. Three B101 findings only narrow the MQTT client's type after a normal `is_connected()` guard; required connection checks remain active under Python optimization. No rule was disabled to conceal these findings.

## Verification argument

The [automated tests](../tests) exercise configuration, failure paths, transport validation and behavior. [scripts/ci.sh](../scripts/ci.sh) runs locked dependency installation, Ruff, format checks, mypy and pytest with coverage. The public quality gate additionally requires workflow/release contracts, CodeQL, dependency review and security scanning. Source inspection and regression tests complement these checks; they are not an independent penetration test.

The 2026-10-13 authenticated GitHub review found zero open dependency alerts, secret alerts or repository security advisories. Four open code-scanning results are Scorecard process findings for best-practices enrollment, maintenance history, fuzzing and branch protection, not identified application vulnerabilities. They remain visible; no protection or security gate is weakened to improve a score. [Public evidence](evidence/assessment-2026-10-13.json) records exact counts, source and local results. Later alerts and versions require a new review.

Published downloads use GitHub HTTPS and SHA-256 inventory. Checksums and a publisher manifest are not independent digital signatures. Signed-release and reproducible-build criteria remain unclaimed until exact published assets are independently verified. The Dockerfile runs as a non-root user; no complete ELF/runtime-hardening audit or accessibility audit is claimed by that fact alone.

## Maintenance commitments and unresolved assumptions

New security-sensitive behavior requires normal, invalid-input and authorization/failure-path tests. Confirmed defects follow [SECURITY.md](../SECURITY.md), including confidential coordination and release notes. Credit reporters in advisories and release notes with their consent; respect anonymous reporting. Dependency changes use the lock file and required checks rather than unreviewed floating deployment updates.

The owner confirmed secure-design and common Python/network-vulnerability knowledge and no external bug, enhancement or vulnerability reports in the preceding 12 months for this project on 2026-10-13. Public issue and advisory review is recorded separately in the evidence. There is no verified successor or recovery arrangement: [GOVERNANCE.md](../GOVERNANCE.md) leaves Silver continuity unmet.

## Warning policy

Pytest treats warnings as errors. Three exact third-party compatibility notices are temporarily filtered: FastAPI's pinned Starlette test client recommends HTTPX2, Starlette imports AnyIO's old BlockingPortal alias, and SlowAPI calls asyncio.iscoroutinefunction on Python 3.14. These are dependency migration notices, not suppressed application failures. Filters are scoped to their emitting module and message; all other warnings remain fatal. Remove each filter when the compatible upstream migration is verified by the full authentication, WebSocket and delivery tests.

## Coverage scope

The combined application and release/workflow/security tests cover 1703 of 2604 executable statements across all tracked non-test Python files (65.40%). Previously unmeasured Python files receive zero credit. Shell, Docker and workflow-language statements are not measured by this figure. The Silver whole-project 80% criterion remains unmet.
