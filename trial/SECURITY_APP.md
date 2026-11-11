# Security app integration

## Scope

The desktop is the viewer/controller; the Hermes execution backend owns enforcement and approvals. SafeAgents remains whole-stack log-only at provisional cutoff 0.88. No new live blocking rules, automatic provider switches, host services, OpenShell changes, or credential changes are part of this deployment.

Source package: `trial/security-plugin/`. Backend target: default-profile `~/.hermes/plugins/local-agent-security/`. Windows desktop target: `%LOCALAPPDATA%/hermes/desktop-plugins/local-agent-security/plugin.js`. The Windows UI and WSL backend are separate installation halves. Do not install into another Hermes profile.

## Managed-run monitoring — source verification, deployment pending

The source now separates Guard recorded completed reviews (Reviewed / PASS / WARN / FLAG / BLOCK), Judge consultations/results, Needs review findings and stored Evaluation & audit totals. Native provider pre-request attempts are session-scoped attempts, not successful proxy receipts. Findings are unassociated with Hermes sessions; only uniquely corroborated managed-run journal records enter current-run attention. Proven journal/capture aliases share one finding while preserving prior labels, explicit review disagreements and separate association conflicts.

`managed_monitor.py` is a read-only, version-aware compatibility reader for the inspected SafeAgents 0.0.1 sources. It verifies protected run paths, metadata, held process-owned lock, process generation/arguments/interpreter, package fingerprints, listener ownership and output-log inode. Only complete, corroborated Monitor records supply totals. Multiline judge errors are reconstructed as one record. Incomplete/ambiguous timestamps, replaced/truncated/racing evidence, unsupported sources and evidence over 8 MiB produce unknown/degraded totals, never fabricated zero. This is recorded completed-review telemetry, not proof that every attempted review was logged.

Capture recording health is independent from judge endpoint/model readiness and observation coverage. Journal consultation metadata distinguishes consulted / explicitly not consulted / unknown. Sidebar rendering and retained action handlers both reject malformed attention envelopes; existing raw-content and ownership guards remain in force. Capture-error findings carry the same attention schema as journal findings (verdict none, no signals, consultation unknown), so one ordinary capture failure cannot invalidate the whole Needs-review envelope — regression-tested by feeding real backend output through the actual frontend validator.

Parent verification: `.venv-checks/bin/python scripts/check.py` passed all offline suites, including **107 trial Python tests and 63 Node UI tests**. The managed reader has 20 isolated tests. A read-only live metadata smoke returned **unverified** for both configured routes: the GLM run is refused because its process inherits a Python import-path override; no managed local run matched. No override values or raw log/capture contents were emitted. The verifier was not weakened and no process, environment, permissions or configuration was changed. These new monitoring changes are **not installed or active yet**; live desktop layout and runtime activation remain pending.

## Sidebar refinement — UI hot-loaded

The sidebar now groups component health without treating an alternative proxy as a failed observed route. Fresh `observing`/`degraded` route evidence permits **Observed route / Other route** grouping; last-recorded provider alone does not. Unknown, stale and failed reads remain explicit, and Diagnostics retains every component's state and detail. Status values are unboxed; Activity owns its filters; native-only lifecycle events omit irrelevant empty-score text; source labels do not imply journal detections are private captures. Missing counts say **Unknown**, not zero.

Expanded metadata uses wrapping, selectable label/value rows and full event IDs. Selected post-hoc labels are visibly distinguished without becoming approvals. Raw capture has a separate local-only surface with unchanged opt-in, size and cleanup guards. Review/inspection errors belong to their event and action; Replay errors stay with Replay. Scope/filter epochs reject delayed errors, while pending-action guards remain enforced. Full locale timestamps and permanently visible Replay controls are unchanged.

Visual follow-up: the three headline states use native icons and decorative state dots inside a compact group. Observation uses the accent only when observing; disabled enforcement remains neutral. Runtime/storage health can use green, while unavailable/degraded/stale reads use amber. State text remains visible independently of color. Component rows use short labels and aligned values; full names and details remain in Diagnostics. The permissions note is shorter, and the chip tooltip contains status without open/close instructions. Toggle behavior and accessible button labels are unchanged.

Verification: **101 Python tests and 56 Node UI tests pass**, using `.venv-checks/bin/python scripts/check.py`. Inert production-element layout fixtures were rendered at 240, 320 and 410 pixels with the installed Codicon font; these use SDK button stubs and do not prove native interaction. DOM element/text-range checks found no horizontal overflow or clipped headline values; the model label wraps at 240 pixels. Both installed JavaScript copies byte-match the tested source. Windows UI Automation verified automatic hot-load in the same running Hermes process, including the new Status overview and observation/enforcement rows; Replay remains present. A cropped live-pane screenshot was inspected for the first refinement. The visual follow-up's live verification used scoped accessibility labels; screen capture was skipped because Hermes was not foreground. No real raw inspection, label mutation, replay launch or session-switch interaction was exercised through the live UI; those paths are covered by isolated tests.

Pre-change JavaScript and deployment hashes are backed up under `/home/imi/.hermes/cache/scratch/security-ui-pre-refinement-wmpt46or`; test/deployment/live-readout evidence is under `/home/imi/.hermes/cache/scratch/security-ui-refinement-20261111`. Backend files remain unchanged. No app/backend restart, configuration edit, model action or enforcement change was performed. Earlier Python activation and source-only journal/replay-recovery limitations below remain separate from this JavaScript refinement.

Visual follow-up evidence and pre-change JavaScript backups: `/home/imi/.hermes/cache/scratch/security-status-visual-20261111` (`backup/`, `deployment.json`, `checks.log`, `layout.json`, `layout.png`, `live-verification.json`).

## Explicit decision-model choices — installed, Python activation pending

Decision-model identity is not tied to the live/replay role. The trusted `trial/decision_models.py` catalogue offers `lite` (**D3 Lite**) and `qev` (**QEV**), requires allowlisted full model-path provenance, and exposes only sanitized identities and weight/template hashes. Missing or unrecognized provenance displays **Unknown model**, never an inferred identity from aliases or filenames.

Adapter startup requires `--model lite|qev` and verifies the selected live backend; it does not start or replace that backend. The pane separates **Live judge** runtime identity from **Replay records** storage health. Offline replay requires an explicit choice in the selector/API and invokes the fixed `replay_model.py --model …` runner. Replay never changes the live judge, executes stored tool calls, or enables enforcement. Existing provisional thresholds are unchanged and are not validated calibration for either model.

New replay rows retain their selected model and actual provenance; legacy `qev.jsonl` remains readable without migration. Scores are presented by recorded identity with separate live/replay roles. Capture, Activity, Review and Replay health remain independent; an unrelated review/replay failure cannot imply lost observation-route coverage. An older running Python backend does not support the new selection contract and must be restarted before these controls can be used. The desktop JavaScript half hot-loads separately; copying files is not proof the new backend is live.

Verification: all **76 Python tests** and **39 Node UI tests** pass, with no new lint findings relative to the source backup. Independent re-review confirmed the isolated trusted-label import and normalized replay-identity fixes. A fresh isolated interpreter loaded the **installed** API and service, verified Lite/QEV choices, rejected missing/unknown replay selections, persisted and read back an owner-only fixture label, and checked no-store headers and unchanged global import paths. That smoke test denied network connections and process creation; it did not access live private captures.

The catalogue, service, API and both UI copies are installed and byte-verified. Pre-change installed files and a hash manifest are backed up under `/home/imi/.hermes/cache/scratch/security-model-deploy-aofzekfo`. No app/backend, adapter, model or proxy process was restarted, and no configuration or enforcement setting changed. **The hosting desktop Python backend still needs an app restart, followed by live readout verification.** Live UI interaction, review of real captures and live model replay were not exercised in this pass.

## Journal identity and replay recovery — source-only fixes

Journal event IDs now use `journal:<path-hash>:<integer-id>:<canonical-content-sha256>` in both the desktop backend and CLI report. Changing any record content, even while reusing the integer ID and timestamp, produces a different identity. Key ordering, JSON whitespace and appending later records do not change an existing identity. Legacy path/number labels remain readable but are never automatically assigned to content-bound events; stale legacy event lookups/label requests return not found. Identical canonical records intentionally share an identity: this is content binding, not a new producer-side generation counter.

Replay status probes the existing owner-only singleton lock without creating it or disturbing a surviving child's inherited lock. A held lock without an owned process handle reports **running**, with unknown ownership/model/start time. Once released, it reports idle with **outcome unknown**, not a fabricated completion or exit code. Invalid/unsafe locks report unavailable. Owned children retain their actual model/timing/exit status and duplicate-start prevention remains in force.

These fixes are source-only; installed plugin files, live backends/models, configuration and enforcement have not been changed. Regression tests use isolated journal stores and harmless fixture children, never real private captures or model replay.

## Installed baseline (before correctness pass)

Backend and Windows desktop UI installed; default-profile config enabled using `hermes config set`. Native `activate_plugin_now(..., in_process=False)` activated the three observation hooks in the running serve/gateway processes with `restart_required=false`; no app or gateway restart was performed. The API hot-mounted on first UI request.

Real app checks: chip appeared automatically, click opened panel, component health/counts rendered, native current-session activity populated. Fixed an initially incorrect runtime-ID lookup to use SDK `focusedStoredSessionId`; query/reveal scope now includes `connectionId`. The current openai-codex session correctly shows Not covered, independently of all five healthy components. Enforcement remains Not enabled.

Verification: 17 Python backend/telemetry tests and 12 Node UI tests passed; installed copies byte-match source. Actual old CLI QEV replay had been verified separately; this UI's review/replay actions are covered by tests (including a real harmless fixture subprocess), not by adjudicating live captures or launching a fresh model from the app. Raw live capture inspection was deliberately not exercised through the assistant.

Remaining acceptance gap: once-only contextual native approval requires the core extension below. No new permission rule or enforcement path was enabled.

## Correctness pass — restarted, live readouts verified

Source implements focused-owner/public-projection guards within the **single-backend, default-profile** deployment described below, independent activity/review/QEV health, separate review/inspection capabilities, unique current-review counts, bounded raw inspection (262144 serialized UTF-8 bytes), consistent native identifiers and retained `api_request_id`, and distinct proposed/completed/error/blocked/cancelled/unknown outcomes. Post-hoc labels display **Expected**, **Unwanted**, and **Insufficient context**; they never grant execution permission. Activity precedes the QEV controls.

Verification: the parent reran 33 Node UI tests and 29 Python service/API/telemetry tests successfully. UI tests use mocked public SDK atoms and REST dispatch, not real transport-routing tests. Backend tests use isolated fixtures, not live private captures. Independent review identified a capture-validator regression; a producer-generated mixed success/error fixture reproduced it and verifies the fix. Legitimate payload-less adapter errors remain visible, noninspectable metadata without invalidating successful captures.

The tested identity/telemetry/service/API files and both UI copies are installed and byte-verified. Pre-change files are backed up under `/home/imi/.hermes/cache/scratch/security-pre-correctness-v5gkhtn7`. The app and backend were manually restarted. Post-restart checks confirmed matching installed source, the current Session row in the running Security pane, and the new Activity/Review/QEV health readouts. Native route metadata identifies the running serve PID, and newly recorded tool metadata retains `api_request_id` with distinct proposed execution state, establishing that the updated telemetry is active. No SDK/core patch or custom desktop rebuild was performed.

The current openai-codex route correctly reports Not covered and enforcement Not enabled. At verification, the UI reported capture/activity/review/QEV and the local proxy healthy; GLM proxy, local decision model and observation adapter were unavailable. Those component statuses were not repaired or restarted during verification. Direct unauthenticated API probes returned 401; no raw captures, review mutations, replay launch, chip interaction or session-switch UI sequence was exercised in this post-restart check. Tests cover those correctness paths, but live readout verification is not full end-to-end acceptance.

### Single-backend assumption and routing limits

This source supports **one configured backend and its default profile only**. Multiple backends and non-default profiles are unsupported (the backend already rejects non-default-profile access to its observation store). It uses resolved `host.state.focusedSessionOwner` (connection/profile), compares it with the existing public `host.state.connectionId` and `host.state.profile` ambient projections, and makes **zero security REST requests** when ownership is unresolved, either projection is unavailable, or the pairs disagree. Queries and retained review/inspection/replay handlers enforce the same guards.

Those public projections are **not authoritative transport routing state**. `ctx.rest` remains ambient; comparing projections is a guard under the explicit one-backend assumption, **not a strong cross-backend transport guarantee**. The plugin neither reads nor requires a private request-scope atom, and does not invent an SDK `apiRequestScope` capability. Supporting multiple backends would require a separately verified owner-routing contract; do not generalize this adaptation to that deployment.

Within the supported deployment, status/activity follow `focusedStoredSessionId`, not the runtime session ID. A compact visible **Session** row displays its short ID with the full ID in a native tooltip; clearing focus displays **None selected**, without an extra RPC or private title query. Owner, public projection, gateway and durable-session changes remount private state and advance the scope epoch, so late responses and A → B → A transitions cannot revive an earlier reveal or query. Hidden/closed panes also discard raw state. Stale/error review and inspection guards, independent subsystem health, explicit inspectability, and the serialized raw byte cap remain enforced.

Retaining `api_request_id` does not establish exact SafeAgents detector-to-tool correlation. Backend action aggregation, incremental JSONL caching, and contextual enforcement remain outside this pass.

## Observation UI contract

- A 24px icon-only shield in the status bar toggles a native dockable Security pane; coverage/enforcement status stays in the standard tooltip (`Tip.label`). No popup. The pane initially stacks below the visible LLM Usage pane in the right sidebar (falls back to the workspace's right edge when no usage pane is visible), is resizable/rearrangeable with the normal layout controls, and has its own scrolling content. Closing/hiding it unmounts raw inspection state. Observation coverage and contextual enforcement are separate states.
- Never infer coverage merely from a listening proxy. Native pre-API metadata records the latest observed route; database evidence must be labeled last-recorded, not guaranteed current.
- Proxy judge captures currently do not carry Hermes session/turn/tool-call IDs. Do not guess their association from timestamp/tool name; show them only in the explicitly all-sessions view.
- Native pre/post tool metadata carries separate session/turn/tool-call/API-request IDs and groups native lifecycle evidence. It never serializes tool arguments, results, task text, history, request payloads, credentials or raw exception text.
- An observation finding says **Observed only — not stopped**. A returned tool result is not independent proof that all side effects succeeded.
- Blocked means the call was prevented from executing. Cancellation can occur mid-tool and must leave effects unknown; errors also do not imply absence of side effects.
- Raw captures are revealed locally only after explicit inspection. Do not send them to chat, clipboard, logs, cloud review, or global memory. Close/scope change clears the reveal; raw content must not enter the shared query cache.
- Labels are retrospective adjudications, never live permission decisions.
- QEV comparison is user-initiated, local, non-executing, one run at a time, with visible state and exit status. No schedule is installed.
- Storage remains plaintext local, owner-only and Git-ignored. No automatic retention deletion.

## Contextual enforcement: blocked on a Hermes API extension

Verified in the running installed Hermes package:
`/nix/store/8y4hn81hp51fmicd9020li00gw2sbi88-hermes-agent-0.0.0/lib/python3.14/site-packages/`

1. `hermes_cli/plugins.py:1087-1207`: approve directives preserve message/rule_key only. Final merged args do not reach the approval gate; another hook may modify args after an individual hook inspected them.
2. `tools/approval.py:1208-1233`: generic tool approval has no structured args or once-only restriction. It shares session/permanent grants and configurable automatic approval behavior.
3. `tools/approval.py:144-178`: shared native queue resolver correlates request IDs but does not validate choices against per-request restrictions before settling.
4. `hermes_cli/approval_transport.py`: immutable requests and allowed-choice validation exist for an optional transport, but the generic plugin action gate has transport disabled. A transport plugin alone does not fix the native flow.

Do not monkeypatch Nix-store core, create a second permission queue, block for human input inside a time-bounded plugin callback, or depend on a middleware exception to prevent execution. The panel must state contextual enforcement is not enabled until the following extension is implemented and verified.

### Smallest core extension to prepare separately

- Add explicit contextual/once-only mode and complete final argument snapshot to approve directives and native approval requests.
- Resolve all argument modifications before the contextual decision; freeze and canonicalize the exact tool name/args/session/tool-call identity presented for consent. Execute that same snapshot after approval.
- Permit only `once` and `deny`; reject other choices at the shared backend resolver before removing the pending request. Validate every response route, including alternate clients and slash-command paths. Never write a contextual consent into any session/permanent allowlist.
- Existing broad grants, smart review, unattended autoapproval, YOLO and approvals-off must not silently satisfy a fresh contextual ASK. No human approval surface means fail closed for that ASK.
- Include full local inspectable arguments plus a safe human-readable effect summary; keep deliberate secret redaction distinct from actual executed payload.
- Reuse native pending request lifecycle, request IDs, settlement and desktop cards. No second UI queue.

### Required end-to-end tests before claiming Active

Use harmless disposable side effects in an isolated fake HOME, never live dangerous operations.

- ASK with no response: side effect absent.
- DENY: side effect absent.
- Allow once for request A: exactly A executes once.
- Stale, duplicate, foreign-session or mismatched request: no other call executes.
- Unsupported session/always/garbage response: reject without settling or persisting a grant.
- Modified args: new consent required; approved snapshot cannot mutate before execution.
- Two concurrent requests: independent settlement.
- UI disconnect, timeout, cancellation and session teardown: no implicit consent; precise held/cancelled/expired outcome.
- Policy/approval failures on sensitive action: fail closed.
- Observation or audit failures: show configured failure policy and independently track execution outcome, including unknown.

A successful hold test is a release qualification, not a permanent green flag: runtime health, route capability and test version must still match. Ordinary work stays low-friction; no blanket ask-before-every-write policy.
