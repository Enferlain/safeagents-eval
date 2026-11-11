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

- Security Status adds native icons, theme-aware state dots, compact aligned component rows and a shorter permissions note. Full component names remain in Diagnostics; the chip tooltip no longer repeats open/close instructions.
- Security sidebar now uses quiet Status values, grouped component-health summaries, one Activity section, readable event metadata, explicit unknown counts, distinct raw inspection and action-local errors. Full timestamps, visible Replay controls and existing observation/permission boundaries are preserved.
- Git publication now uses an allowlist: private observations, local environments, downloaded benchmark bundles, generated artifacts and unreviewed workspace paths remain local.

### Fixed

- Journal review identities now include a canonical record-content hash, shared by CLI reports and the desktop backend. Reused integer IDs cannot transfer labels to different records; ambiguous legacy labels remain stored but do not bind to new identities.
- Replay status detects a singleton lock retained by a surviving child after backend state loss. Unknown ownership/model/timing and unknown completion outcomes remain explicit; duplicate replay prevention is unchanged.

### Unchanged

- Existing model paths, live launchers, installed Hermes plugin files, services, private captures and historical run artifacts are not moved, deleted, reconfigured or redeployed by this source cleanup.
