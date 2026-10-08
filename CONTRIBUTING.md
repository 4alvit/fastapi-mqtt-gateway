# Contributing

An authenticated REST/WebSocket bridge to MQTT with per-topic access controls and bounded delivery.

## Questions, bugs and proposals

Use [GitHub Issues](https://github.com/4alvit/fastapi-mqtt-gateway/issues) for questions, bug reports and feature proposals. Search existing issues first. Describe the affected version/commit, expected and actual behavior, minimal reproduction and relevant environment. Remove tokens, private endpoints, household identifiers and personal data from examples. Security vulnerabilities use the confidential process in [SECURITY.md](SECURITY.md).

Anyone may propose a change through a pull request. Discuss compatibility or architectural changes in an issue before a large implementation. Maintainers aim to acknowledge actionable reports within 14 days; security reports follow the security policy. No paid support or response-time guarantee is implied.

## Development and validation

Clone the repository, create a branch from `main`, and use the Python version and dependencies declared by the project and CI. Run from the repository root:

```sh
uv sync --locked --all-extras
bash scripts/ci.sh
```

The gate runs Ruff, formatting, mypy and pytest with coverage. Tests exercise authentication, MQTT topic ACLs, WebSocket security, delivery acknowledgements, reconnect behavior and service readiness. Use synthetic credentials and local broker fixtures.

For a bug fix, add a regression test that fails before the fix and passes afterward. For new functionality, test normal behavior, invalid input and relevant authorization/error paths. Preserve existing checks; do not lower coverage gates or ignore findings merely to obtain a green build. Python code follows the configured formatter/linter where present and normal PEP 8 conventions otherwise. Keep shell, YAML and generated examples compatible with their declared tools.

## Review and compatibility

Keep pull requests focused and explain the problem, resulting behavior, compatibility impact and exact validation performed. Update the user-facing documentation when changing configuration, interfaces or operational behavior. Call out tests not run and their prerequisites. Maintainers review changes through GitHub pull requests and required CI; automated review is supplemental. Contributions are provided under the repository's [MIT license](LICENSE); a contributor must have the right to submit the work.

Follow `RELEASING.md` and the release workflow. Include authentication/ACL changes, API or topic schema compatibility, security fixes and migration requirements in release notes.

## Source and interfaces

- [`src/fastapi_mqtt_gateway`](src/fastapi_mqtt_gateway)
- [`README.md`](README.md)
- [`RELEASING.md`](RELEASING.md)

See the [OpenSSF evidence index](docs/openssf-evidence.md) for the current assessment scope and outstanding verification.

Release changes must update `CHANGELOG.md` under a unique `## [X.Y.Z]`
base-version heading, including nonempty `### Upgrade` and `### Security`
sections. The release controller reads that file from the exact packaged source
commit, validates its Git blob, and retains build provenance in the public notes.
Run `python3 -m unittest discover -s .github/release-tests -p "test_*.py"`
after changing release tooling.
