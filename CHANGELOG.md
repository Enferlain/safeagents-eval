# Changelog

All notable changes are recorded here. This project follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) structure.

## [Unreleased]

### Added

- Apache-2.0 license for original project contributions, with separate third-party notices and benchmark licensing boundaries.
- Root project guide, development instructions and benchmark/runtime prerequisites.
- Locked Python dependency graph, including an immutable SafeAgents upstream commit.
- A single data-free Python/UI regression command using a dedicated development environment and temporary agent homes.
- Explicit optional reference-conformance checks; native checkpoint/CUDA tests are not part of the default command.
- Commit-pinned, read-only GitHub Actions configuration running the same check command.
- Regression tests for publication exclusions, the default check plan, CI configuration and README links.

### Changed

- Git publication now uses an allowlist: private observations, local environments, downloaded benchmark bundles, generated artifacts and unreviewed workspace paths remain local.

### Unchanged

- Existing model paths, live launchers, installed Hermes plugin files, services, private captures and historical run artifacts are not moved, deleted, reconfigured or redeployed by this source cleanup.
