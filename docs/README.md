# Documentation

A FastAPI gateway that bridges HTTP clients and MQTT, with the release tooling,
image packaging and security evidence around it.

## Release operation

- [CI and release operations](release-workflow.md) — `.release-policy.json` as source of truth.
- [CI orchestration](CI-ORCHESTRATION.md) — how the quality gate and release pipeline divide the events.
- [Project release packaging and versions](release-packaging.md) — the source tarball and the amd64/arm64 image.
- [Version plans and manifest synchronization](VERSIONING.md) — the versioning opt-in.
- [Release helper TLS profile](RELEASE_TLS_PROFILE.md) — the qualified `gh` transport profile.

## Evidence and limits

- [Security assurance and evidence limits](security-assurance.md) — what was assessed at the named source revision and what was not.
- [TLS certificate key policy](tls-policy.md) — the verification applied to owned HTTPS clients.
- [OpenSSF Best Practices evidence](openssf-evidence.md) — the evidence index behind the self-assessment.
- [Release-candidate fuzz testing](fuzzing.md) — the illustrated RC fuzzing process.
- [Fuzzing release candidates](rc-fuzzing.md) — the reusable bounded fuzz workflow.
- [Release read recovery and rerun validation](release-read-safety.md) — the two fixes carried from the shared toolkit.
- [Early final-build runner check](runner-preflight.md) — why the check runs before the final build.
