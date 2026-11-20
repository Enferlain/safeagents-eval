# Changelog

All notable changes are recorded here. This project follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) structure.

## [Unreleased]

### Changed

- Repository split: the security plugin now lives in `Enferlain/hermes-safeagents` (extracted at `pre-split-baseline`, history preserved; plugin identity renamed from `local-agent-security`). XAAB/comparison policy reads point at the plugin's canonical `judge_adapter.py` instead of the duplicated `trial/` copy; runtime state (observation store, journals, `.venv-trial`) deliberately still resolves in this checkout until the state-relocation step, and the duplicated trial plugin sources stay until the eval cleanup step.

### Fixed

- Stack-start cold-boot robustness: the decision-model wait deadline is 120s (measured model load is ~2s; the real variable is Windows process start after reboot), dependents declared via `requires` are skipped — not launched blind — when it expires, and the judge adapter retries its identity probe for a bounded 60s window (PROBE_RETRY_SECONDS) instead of dying on the first 503 from a still-starting sibling. Same probe, same verification, retried.

- Managed-monitor parser: single-line `[safeagents] upstream error:` notices (new in upstream dcf38e0, emitted on httpx.HTTPError with possibly-empty message) now count as runtime errors with totals intact — previously one such line aborted record parsing entirely, degrading the run with zero totals. Self-protection FLAG rows (reason-only, no signals, no judge) verified to corroborate correctly. Live result after fix: 120 records, 1 runtime error, degraded-with-totals.

### Changed

- Upgraded the trial install to upstream `dcf38e0` (sinks, self-protection, atif-scan off the event loop, OpenCode 2.x): both venvs reinstalled from the refreshed upstream clone (copy install, not editable — the provenance check needs the physical site-packages dir), SUPPORTED_SOURCE manifest extended to the 16-file set with recomputed hashes, proxy restarted and the managed run re-verified live on the new package with zero runtime errors. Sinks remain opt-in (no `--sink` passed yet); self-protection is now active in the guard.

- Flagged `harness-hub/safeagents-dotenv-plugin` (scanner-plugin candidate, license undeclared; not loaded).
- Flagged judge-bench-v2 leaderboard candidates (record-only, nothing downloaded): clef-flash, FastBrain-9B (GGUF path), gliclass-v4-edge, GLiNER2.5-Decide, niti-gate-vegaml-4b (CC-BY-NC caveat). Noted v2 dataset refresh path (661 rows; v1 preserved as a revision).

- Tier-1 Hermes↔SafeAgents correlation: captures bind to the request whose interval covers them (one request at a time per session), landing on native session/turn identity. Unique same-tool call in the response binds the exact `tool_call_id` (`exact`); batched same-tool calls bind session+turn only (`request`, never guessed); missing/ambiguous evidence stays unassociated. Merged journal findings inherit session attribution (`session_attribution`) while capture-join quality (`identity_status`) stays separate. Fail-closed throughout; verified against the live corpus (gray-zone findings now session-attributed).

- Monitoring stack control: `GET /stack` (liveness + launchability of decision model / judge adapter / managed proxy by port probe) and `POST /stack/start` (fixed launch recipes only, per-component idempotence, no request parameters, no stop endpoint). When everything is down and launchable, the pane's live-status surfaces collapse into a single "Monitoring stack is off." line with a Start monitoring button; stored history, Activity and Replay stay available. Start failures are action-local; partial or unlaunchable states keep the detailed view. Backend routes activate on gateway restart; JS hot-loads.

- Pane liveness diagnostics: a heartbeat re-render keeps a "Pane refresh: Ns ago · status data Ns old" line in Diagnostics, so a frozen pane (dead poller or caught render error) becomes visible instead of silently unclickable. Timer-less sandboxed contexts degrade gracefully. Instrumentation for the unresolved 2026-11-12 click-deadness.
- Activity rows now show their detail text at the first Details level; only raw IDs and per-event scores stay nested under Technical identity (fixes Details-disclosure rows that contained nothing but the nested toggle).
- Regression: review buttons must re-enable and accept label writes after a transient status/subsystem outage clears.
- `REPORT.md`: flagged-candidate entry for Perplexity's `pplx-decider-flash-v1.3` (Qwen3.5-4B decider distilled from a 27B sibling) — native one-question noul fit for the judge bench, plus the constraint that it needs the bundled noncausal `pplx_decider` Transformers source rather than a GGUF adapter path.

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

- Pane Replay fails everywhere with an instant `exit 1` since the 2026-11-13 boot: `powershell.exe -EncodedCommand` (used for owned-process discovery and cleanup) exits 1 when spawned from PTY-less parents such as the gateway service or background terminals, and failed cleanup also left orphaned replay servers holding port 18874, which then failed every subsequent attempt at the port check. Replay now owns its temporary server WSL-side: the `/init` interop wrapper's `/proc/<pid>/cmdline` is verified against the exact executable and launch arguments, and cleanup terminates that verified wrapper (which stops the Windows child). The PowerShell round-trip is removed entirely; ownership guarantees are unchanged (exact argv match, never a port's unknown occupant). Verified live: QEV replay scored 10 pending captures and released the port from a PTY-less background run.

- Capture-recording write failures no longer degrade independent judge readiness or observation-route health.
- Recorded uvicorn runtime errors (including chained tracebacks) no longer erase corroborated review totals; they keep the run degraded and are counted, never rendered.
- A single failed capture record no longer suppresses the whole Needs-review feed; capture-error findings share the attention schema with journal findings.
- Technical capture failures are excluded from Needs review, are not reviewable in any view or via the label API, and surface as a failed-judge-attempts count in Diagnostics; the recorder component groups under Storage.
- Proven journal/capture aliases share one finding and preserve review labels; review disagreements and association conflicts remain distinct. Ambiguous duplicate capture IDs cannot be inspected.
- Journal review identities now include a canonical record-content hash, shared by CLI reports and the desktop backend. Reused integer IDs cannot transfer labels to different records; ambiguous legacy labels remain stored but do not bind to new identities.
- Replay status detects a singleton lock retained by a surviving child after backend state loss. Unknown ownership/model/timing and unknown completion outcomes remain explicit; duplicate replay prevention is unchanged.

### Unchanged

- Existing model paths, live launchers, installed Hermes plugin files, services, private captures and historical run artifacts are not moved, deleted, reconfigured or redeployed by this source cleanup.
