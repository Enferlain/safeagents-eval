# Changelog

All notable changes are recorded here. This project follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) structure.

## [Unreleased]

### Added

- Pane liveness diagnostics: a heartbeat re-render keeps a "Pane refresh: Ns ago · status data Ns old" line in Diagnostics, so a frozen pane (dead poller or caught render error) becomes visible instead of silently unclickable. Timer-less sandboxed contexts degrade gracefully. Instrumentation for the unresolved 2026-11-12 click-deadness.
- Activity rows now show their detail text at the first Details level; only raw IDs and per-event scores stay nested under Technical identity (fixes Details-disclosure rows that contained nothing but the nested toggle).
- Regression: review buttons must re-enable and accept label writes after a transient status/subsystem outage clears.

- `gemini_run.py`: gemini-decision-1 (Google Decisions API, `noul`) backend for the frozen SafeAgents judge bench — state serialized with the exact System One `js` macro, identical frozen question, per-row predictions/usage/latency, quota-aware pacing and a repair path for rate-limit errors. First result (562/562 valid after repairing 23 quota errors): AUROC 0.983, Brier 0.030, recall 96.7% / false-block 20.5% at the naive 0.5 cutoff. Cross-suite follow-ups (xaab/ and page/ `gemini_run.py`, same frozen payloads): XAAB AUROC 0.989 with 95.6% recall @ 0.5 at zero false positives; PAGE AUROC 0.994 with 95.0% adversarial blocked @ 0.5 and 2.7% benign false-flags. Local comparison on identical inputs: Lite 0.944 / 0.911 / 0.755.
- `sweep_gemini.py`: offline threshold sweep and matched-FPR comparison over saved predictions; no model calls.
- Read-only current-managed-run telemetry for recorded completed SafeAgents reviews and judge consultations, with process/lock/listener/log/source validation and unknown totals for incomplete evidence. Native provider attempts remain separate from proxy receipts.
- Repeatable `--plugin` scanner options (order-preserving, file-path specs) in managed-run verification, and static import-resolution provenance: the first importable SafeAgents package on the process's path must hash-match the supported source across an exact pinned manifest of every module; PYTHONHOME stays refused and uv-style hardlinked installs verify by content.
- Observation: Observing now requires a verified managed run for the watched route; an unverified-but-reachable proxy reports Degraded.
- Security Guard, Judge and Needs review sections with explicit run/history scopes, sanitized findings, and separate Evaluation & audit totals.
- Apache-2.0 license for original project contributions, with separate third-party notices and benchmark licensing boundaries.
- Root project guide, development instructions and benchmark/runtime prerequisites.
- Locked Python dependency graph, including an immutable SafeAgents upstream commit.
- A single data-free Python/UI regression command using a dedicated development environment and temporary agent homes.
- Explicit optional reference-conformance checks; native checkpoint/CUDA tests are not part of the default command.
- Commit-pinned, read-only GitHub Actions configuration running the same check command.
- Regression tests for publication exclusions, the default check plan, CI configuration and README links.

### Changed

- Security dashboard final cleanup: quiet two-line Guard/Judge state replaces Unknown grids when no managed run is verified; Hermes provider-attempt history moved out of Guard into its own section; findings are signal-first with the validated journal tool as secondary metadata; the judge summary is deduplicated (`D3 Lite · Allow · 0.436`, outcome distinct from any threshold interpretation); session-correlation wording is user-language; event IDs and technical identity are demoted to a nested disclosure.
- Contextual-enforcement documentation rewritten around the settled no-fork chain (decision → correlation → public plugin surfaces → ALLOW/native ASK/DENY), with the YOLO/remembered-grants caveat as the open question.

- Security Status adds native icons, theme-aware state dots, compact aligned component rows and a shorter permissions note. Full component names remain in Diagnostics; the chip tooltip no longer repeats open/close instructions.
- Security sidebar now uses quiet Status values, grouped component-health summaries, one Activity section, readable event metadata, explicit unknown counts, distinct raw inspection and action-local errors. Full timestamps, visible Replay controls and existing observation/permission boundaries are preserved.
- Git publication now uses an allowlist: private observations, local environments, downloaded benchmark bundles, generated artifacts and unreviewed workspace paths remain local.

### Fixed

- Capture-recording write failures no longer degrade independent judge readiness or observation-route health.
- Recorded uvicorn runtime errors (including chained tracebacks) no longer erase corroborated review totals; they keep the run degraded and are counted, never rendered.
- A single failed capture record no longer suppresses the whole Needs-review feed; capture-error findings share the attention schema with journal findings.
- Technical capture failures are excluded from Needs review, are not reviewable in any view or via the label API, and surface as a failed-judge-attempts count in Diagnostics; the recorder component groups under Storage.
- Proven journal/capture aliases share one finding and preserve review labels; review disagreements and association conflicts remain distinct. Ambiguous duplicate capture IDs cannot be inspected.
- Journal review identities now include a canonical record-content hash, shared by CLI reports and the desktop backend. Reused integer IDs cannot transfer labels to different records; ambiguous legacy labels remain stored but do not bind to new identities.
- Replay status detects a singleton lock retained by a surviving child after backend state loss. Unknown ownership/model/timing and unknown completion outcomes remain explicit; duplicate replay prevention is unchanged.

### Unchanged

- Existing model paths, live launchers, installed Hermes plugin files, services, private captures and historical run artifacts are not moved, deleted, reconfigured or redeployed by this source cleanup.
