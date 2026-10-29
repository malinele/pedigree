# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-30

### Added

- Initial release.
- Attestations stored in git notes, with optional HMAC signing and binding to the commit sha.
- Author classification, where an attestation wins over commit trailers.
- Declarative TOML policy with path globs and requirement types.
- `verify` with text, JSON and SARIF output.
- Provenance `report`: attested coverage, AI line share and per-tool counts.
- Guarded-runner run-directory ingestion via `attest --from-cco-run`.
- Zero runtime dependencies.
- 170+ tests.
