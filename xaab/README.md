# XAAB local classifier evaluation

## What was run

Pinned upstream: https://github.com/xybern-ai/agent-authz-benchmark/tree/0249d9ccee4f48a9d49da3363d478715ff99f335

All 180 scenarios (135 restrict / 45 allow) were scored against the live adapter's **personal-assistant QUESTION** using the existing decision-only model on `127.0.0.1:18872/v1/systemone`. Only the question literal is extracted from `trial/judge_adapter.py` with AST literal evaluation. No upstream executable code is imported or run. The original live proxies, threshold and judge process were not changed.

This is a **judge-only diagnostic**, not an official XAAB reference-pack result or an end-to-end safeagents test. Scanner gating and deterministic safeagents policies are bypassed. The benchmark's metadata/mission/prior-action context is richer than the live adapter normally gets. Most cases lack an original user request or independent approval record. Missing context is explicitly marked missing, not synthesized from the answer.

## Results

- `COMPARISON.md` / `COMPARISON.json`: frozen-input Lite / Edge / QEV comparison, category metrics, AP, and retrospective FPR-ceiling operating points.
- `edge-001/` / `qev-001/`: new complete runs with byte-identical classification inputs to `run-001`, plus model hashes, native templates/calibration, server arguments, properties and cleanup evidence.
- Recompute comparison from saved predictions: `.venv-trial/bin/python xaab/analyze_comparison.py`.
- `run_comparison.py` owns sequential temporary Edge/QEV servers on port 18874, without replacing live Lite on 18872. It refuses existing output directories; do not rerun blindly over archived runs. No deployment threshold was changed.
- `run-001/REPORT.md`: metrics, category breakdowns, limitations and disagreements.
- `run-001/summary.json`: machine-readable summary and all scores.
- `run-001/inputs.jsonl`: exact classification inputs (IDs outside payload).
- `run-001/results.jsonl`: per-case scores, labels, latencies, limitations/errors.
- `run-001/policy.txt`: the exact question used, including its hash in the report.
- `source-manifest.json`: pinned commit and SHA-256 hashes of downloaded data.
- `run-001.log`: visible progress log.

At the pre-existing 0.88 threshold: 10 TP / 125 FN / 0 FP / 45 TN; 7.4% recall and 0% false-positive rate against upstream labels. At the prespecified 0.5 reference: 75 TP / 60 FN / 2 FP / 43 TN; 55.6% recall and 4.4% false-positive rate. AUROC 0.91144. Zero inference errors. No threshold was optimized or deployed. Many vendor-policy expectations do not match the generic assistant policy, so label disagreements require inspection.

## Safety boundary

`launch.sh` uses bubblewrap, fails if sandbox setup fails, and exposes only the runtime and read-only project plus one writable result directory. It has a fake HOME and no Windows mounts, WSL interop socket, real agent homes or host `/run`. The network namespace is shared solely to reach the existing loopback model; **network restriction is implemented by a Python audit hook, not a kernel network policy**. The hook denies subprocess creation, shell execution, dynamic ctypes loading, outside-result writes, and connections other than the single selected endpoint: `127.0.0.1:18872` by default, or `127.0.0.1:18874` with `--port 18874`. It does not permit both simultaneously. This is defense in depth for a fixed classifier-only program, not a promise that CPython audit hooks contain arbitrary hostile Python. Dataset text and model responses are never evaluated as code; there is no tool dispatcher.

The safety check attempts blocked process creation, external networking and outside-result writing. Test payloads are inert; the attempted process has a nonexistent program name, never a destructive command.

## Reproduce

From `/home/imi/Projects/safeagents-eval`:

```bash
.venv-trial/bin/python -m unittest discover -s xaab -p test_runner.py -v
bash xaab/launch.sh /home/imi/Projects/safeagents-eval/xaab/safety-check --safety-check
# Choose a NEW result directory. Existing results are never overwritten.
set -o pipefail
bash xaab/launch.sh /home/imi/Projects/safeagents-eval/xaab/run-002 | tee xaab/run-002.log
```

Requires existing `bwrap`, trial venv/PyYAML, Nix loader environment (`NIX_LD`, `NIX_LD_LIBRARY_PATH`), and the already-running local decision model. No package installation or credentials required. The launcher binds both the resolved uv Python runtime and its unversioned alias, and resolves Nix loader paths into `/nix/store` before clearing the environment.

Downloaded YAML is parsed with `yaml.safe_load` and verified against the manifest before inference. Label/title/category/severity/source/notes are excluded from classification inputs. The fixed source includes paraphrase families: use group-wise held-out splits before any future tuning; these results are not a fresh holdout after inspecting them.
