# Local SafeAgents observation

## Current behavior

The adapter on `127.0.0.1:18873` captures each judge request in `trial/private-observation/calls.jsonl`, including exact System One input, the actual live judge's full-precision score, provisional would-block marker, input/policy hashes, backend weight/template/calibration/build provenance, timestamp and correlation UUID. Errors are recorded as errors, never fabricated allow scores. Parse failures without a usable input remain errors and are not replayed.

Model choice is explicit: `lite` (D3 Lite) or `qev` (QEV), defined in `decision_models.py`. Adapter startup validates `--model` against the backend's actual model provenance; it does not launch or replace that backend. Replay has a separate model choice and leaves the live judge unchanged. The existing processes and provisional 0.88 threshold were not changed by this implementation. This cutoff is not validated deployment calibration for either model, and must not be presented as QEV calibration. Both proxies remain whole-stack **log-only**: deterministic flags and model verdicts do not enforce blocking. Capturing failed records must not change the returned judge decision; capture-write failures appear in the adapter log and `/health` capture_errors counter.

Only model-scored gray-area calls reach this capture point. Deterministic bypass flags are read separately from the existing proxy journals when generating a report. Calls with no scanner signals are not recorded by this mechanism; absence of capture is not evidence that a route was watched or safe. Correlation IDs are added to the judge reason, so new existing-journal entries can be matched to captures without modifying journal files.

The judge sees SafeAgents' selected/truncated user text, tool arguments and findings—not complete authorization history. Capture preserves exactly what the judge received; it does not magically add missing context. Distinguish authorized / unwanted / insufficient-context when reviewing.

## Privacy

Raw task text and arguments can contain secrets. The dedicated directory is owner-only 0700; records, reports and local runtime logs are 0600. Root `.gitignore` excludes the observation directories, existing live journals and adapter logs; the private directory also has an ignore-all file. This is plaintext local storage, NOT encryption, automatic secret redaction or protection against other processes running as the same user. Nothing is automatically uploaded, committed, synced, or deleted. Retention is indefinite until deliberately cleaned up.

Default status/report/replay output contains counts and metadata only. `review.md` omits tasks, arguments, reasons and free-text notes. Do not attach raw JSONL, raw-view output or local notes to chat/cloud tools. Read selected content only in your own local terminal with explicit opt-in.

## Daily use

From `/home/imi/Projects/safeagents-eval`:

```bash
# Metadata-only counts. No inference.
.venv-trial/bin/python trial/observe.py status

# Choose a temporary comparator on 18874, replay pending real captures, then stop
# only that exact owned server. No submitted action is executed.
.venv-trial/bin/python trial/replay_model.py --model lite
# Or select QEV explicitly:
.venv-trial/bin/python trial/replay_model.py --model qev
# Optional per-run cap:
.venv-trial/bin/python trial/replay_model.py --model qev --limit 50

# Build/update a metadata-only report, including separately classified
# deterministic flags from both existing journals since capture began.
.venv-trial/bin/python trial/observe.py report
# Open locally: trial/private-observation/review.md

# Inspect ONE selected raw capture in your own terminal, not through chat.
# Replace RECORD_UUID with an actual UUID from the report.
.venv-trial/bin/python trial/observe.py show RECORD_UUID --include-content

# Store a human adjudication; labels never alter enforcement.
.venv-trial/bin/python trial/observe.py label RECORD_UUID authorized
.venv-trial/bin/python trial/observe.py label RECORD_UUID unwanted
.venv-trial/bin/python trial/observe.py label RECORD_UUID insufficient-context
# Rebuild the report after labeling.
.venv-trial/bin/python trial/observe.py report
```

`replay_model.py --model lite|qev` launches only the selected catalogue model when eligible inputs exist, uses local-only/no-agent/offline server flags, verifies exact model ownership and port availability, and leaves the live judge alone. Launch/cleanup logs are owner-only within the observation store. No daemon or schedule was installed. Replay requires actual local provenance matching the selected model, forbids remote endpoints, disables environment HTTP proxies and redirects, and uses an audit guard that denies subprocess creation and connections outside 127.0.0.1:18874 inside the replay worker. This guard is defense in depth around fixed data-only code, not a claim to sandbox arbitrary hostile Python. The trusted parent only starts/stops its exact owned comparator server, never captured commands. `replay_qev.py` is a compatibility wrapper selecting QEV explicitly, not the app's generic replay entrypoint.

New replay results append to `replays.jsonl` with the selected model key and actual provenance. Existing `calls.jsonl` and legacy `qev.jsonl` are read without migration or rewriting. Reports/UI label source and replay scores by their recorded model identities, not by the live/replay role or filename; missing provenance is **Unknown model**. Different weight/template identities remain distinguishable.

Repeated replay skips already-scored matching input/policy/model identities. Partial progress is durable; invalid probabilities/backend errors are retained and reported, not disguised. Verification probes are separate and skipped unless `--include-probes` is explicitly supplied. Replay would-flag markers reuse the source capture's provisional cutoff for comparison only—not calibration for the selected comparator. The default absolute score-gap threshold 0.3 is a triage heuristic, not a scientific disagreement metric.

The report reads legacy journals without changing them. It separates correlated model records, deterministic flags, judge failures, legacy/unmatched rows and missing sources. Journal labels use content-bound `journal:<path-hash>:<number>:<canonical-content-sha256>` IDs. Reusing a number for different record content cannot transfer a review; key ordering, whitespace and later appends preserve the original identity. Identical canonical records share an identity. Old path/number labels remain stored and readable but are not automatically attached to content-bound events. Raw journal content remains in the original owner-only journal; the capture `show` command accepts capture UUIDs only. Historical rows before the first capture are omitted and counted. Verification-origin captures are explicitly separated from traffic; no performance claim should include them.

## Route coverage

Use a watched named provider (`safeagents-glm` or `safeagents-local`) for normal work. A session on `openai-codex` is NOT routed through either proxy. The current capture layer neither changes session providers nor detects unproxied calls. No automatic provider switch was made. Either replay model receives the same recorded input/policy as the live judge; public benchmark prompts are not substituted.

## Health and manual startup

Adapter log: `trial/logs/judge-observation-18873.log`.

`http://127.0.0.1:18873/health` exposes metadata-only status, capture-enabled flag, write-error count, provisional threshold, policy hash, sanitized model identity/fingerprints and last observation ID/time. Startup verifies the selected backend and weight hash; before scoring, the adapter checks that the backend still reports the same catalogue model. Changing the decision model requires deliberately restarting the adapter to refresh provenance. This identity check does not rehash/revalidate replaced weight files on every request.

The app distinguishes **Live judge** identity/runtime status from **Replay records** storage health. Healthy replay records do not mean a comparator model is running. Its Replay model selector requires an explicit supported choice; it cannot silently switch the live judge. An older running backend without the new model-selection API is not replay-compatible and must be restarted before using the updated controls.

Manual startup after the chosen backend is already running on 18872 (choose the matching `--model`; this command does not start the model):

```bash
umask 077
.venv-trial/bin/python -u trial/judge_adapter.py 18873 --model lite \
  >> trial/logs/judge-observation-18873.log 2>&1
```

No service/autostart changes were made. `OBSERVATION_DIR` can select a dedicated alternate store; LLAMA_URL and THRESHOLD retain their prior settings. A source backup is `trial/judge_adapter.py.pre-observation`.

## Verification

```bash
.venv-trial/bin/python -m unittest discover -s trial -p 'test_observ*.py' -v
.venv-trial/bin/python -m unittest discover -s trial -p test_judge_adapter.py -v
.venv-trial/bin/python trial/verify_observation.py
# Optional real local verification of inert probes; choose the comparator explicitly:
.venv-trial/bin/python trial/replay_model.py --model qev --include-probes
```

`verify_observation.py` classifies inert strings through the real SafeAgents Guard and HTTP adapter, verifies full-precision private capture and journal correlation, and checks a deterministic undeclared-tool flag remains nonblocking in log-only mode. It has no tool dispatcher and executes none of the proposed calls. Probe artifacts are isolated under `trial/observation-verification/`; captured probes carry verification origin. Unit tests use inert fixtures only.
