# Project release packaging and versions

The source tarball and the existing linux/amd64 + linux/arm64 OCI image remain the release payloads. The image installs the overlaid Python project, and both architecture configs carry the checked SemVer package label.

## Owned version fields

The API settings and public Python `__version__` now share `src/fastapi_mqtt_gateway/_version.py`. This constant is declared alongside the project manifest and owned lock entry, so a preparation PR or build overlay updates all three. A configured `APP_VERSION` may still override the existing application setting; default packaged identity comes from the frozen plan.

The complete selectors are in [the release policy](../.release-policy.json). Only owned package metadata is synchronized. Third-party dependency versions, lock hashes and independent component versions are preserved. The committed source contains a numeric `X.Y.Z` base; candidate overlays are build outputs and must not be committed.

## Candidate identity and stable compatibility

This repository explicitly retains `promote-bytes`. For base `1.2.3`, beta 7 has tag `v1.2.3-beta.7`, SemVer package identity `1.2.3-beta.7` and Python identity `1.2.3b7`. A nightly's full tag includes the frozen UTC timestamp/run/attempt; its Python version is `1.2.3.dev<run * 1000000 + attempt>`. RC 7 has tag `v1.2.3-rc.7`, while its embedded package version is already `1.2.3`. Stable promotion copies that accepted RC's bytes; it does not relabel or rebuild the payload. The manifest and plan retain the RC origin after promotion.

Source-archive filenames use the checked SemVer package identity. Python filenames follow their backend's PEP 440 spelling. Publication is identified by the full frozen tag, source SHA and checksums, not by an archive basename alone. No stable publication, deployment, package-registry import or acceptance approval is caused by preparing a version or running local tests.

## Preparation and build order

From clean current default-branch HEAD, use:

```sh
python3 scripts/version_plan.py check-base
python3 scripts/release.py prepare-version --dry-run
python3 scripts/release.py prepare-version --pr
```

The preparation command refreshes tags and preserves an existing unreleased base; explicit `--bump minor`, `--bump major` or `--version X.Y.Z` records intentional changes. Review every owned field and the release notes together. The allocator reserves the full candidate and counter on `release-version-state`; failed runs may leave gaps. Never reset that ledger to reuse an identity.

The release workflow uploads one `.release-plan.json` before building. Each build job downloads that exact artifact and runs `version_plan.py sync`, producing `.release-inputs.json`. The packager validates the requested base/channel against the saved plan and snapshots the overlaid tracked inputs. After package-specific checks and checksums, `stage_release_assets.py` copies final payloads into `release-assets/package` and creates `release-inputs-package.json`. The publisher requires complete declared artifact metadata and verifies every payload's size/hash against the receipt. A missing platform, mismatched package version, changed version input or uncovered payload prevents publication.

## Local reproduction and verification

A publishable local reproduction requires a disposable checkout of the manifest's exact source commit and its saved `version_plan` object as `.release-plan.json`. Do not invent a tag, sequence or native counter. Apply the saved plan before calling the adapter:

```sh
python3 scripts/version_plan.py sync --plan .release-plan.json
python3 scripts/release_version_adapter.py X.Y.Z beta
bash scripts/package-release.sh X.Y.Z beta
python3 scripts/stage_release_assets.py --target package --pattern 'release-dist/*'
```

Use the saved plan's actual base/channel. Without a plan, local packaging can check committed base metadata but cannot stage publishable assets. Do not treat such a development archive as a numbered beta or RC.

Run the release contracts with Python 3.12+ and the pinned workflow-contract dependencies:

```sh
python3 -m unittest discover -s .github/release-tests -p 'test_*.py'
python3 -m unittest discover -s .github/workflow-tests -p 'test_*.py'
```

From the toolkit checkout, `python3 scripts/install_release.py /path/to/this/repository --check` detects generated drift. Update the shared toolkit first, render again, then recheck project-specific adapters and tests. Do not edit vendored files directly. The pinned toolkit revision in the policy documents the reviewed source.

## Acceptance and recovery

Run the API and MQTT application tests, then exercise the exact candidate with the intended broker, credentials, subscriptions and deployment configuration before stable approval. The existing zero coverage threshold is preserved; this migration makes no additional runtime acceptance claim.

A changed source, lockfile, packaging recipe or policy needs a new candidate and new acceptance. A failed build cannot be repaired by editing an uploaded archive, receipt or plan. Correct the source in a reviewed commit and request a fresh candidate; retain earlier evidence for diagnosis. Restore a previously accepted immutable artifact for operational rollback, with configuration/data compatibility reviewed separately. See [shared version-plan rules](VERSIONING.md) and [the release runbook](release-workflow.md) for allocation recovery, evidence retention and publication controls.
