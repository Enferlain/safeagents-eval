# Benchmark and runtime prerequisites

The default regression suite needs none of the data, weights or native runtimes below. This source publication intentionally leaves downloaded bundles and historical run artifacts local.

## Historical SafeAgents judge benchmark

- Source: [harness-hub/safeagents-judge-bench](https://huggingface.co/datasets/harness-hub/safeagents-judge-bench).
- Local historical snapshot: test split, **562 rows**.
- Recorded SHA-256: `b89c16a72f2b210867b7b76eafe3d9417255d8069a50d42a810e153a8be9553a`.
- Metadata: [`dataset-provenance.json`](../dataset-provenance.json); historical methodology/results: [`REPORT.md`](../REPORT.md).

The original dataset revision was not recorded. The hash identifies the historical local JSON bytes; it does not reconstruct them. A current upstream download can differ and must not be called an exact reproduction of the report. `dataset.json`, raw predictions and model/runtime metadata are not bundled.

**`run.py` is a real evaluation launcher, not a smoke-test command.** Its `dataset()` helper downloads the current dataset if the local file is absent, and its evaluation entry point can start a real native model. The default check runner never calls those paths. Obtain and verify the intended dataset separately before deliberate benchmarking; do not regenerate the historical provenance file merely to make a new download appear to match it.

## Intern reference conformance and CUDA

The optional `references` suite expects:

```text
dataset.json
references/Intern-Decision-0.8B/inference.py
references/Intern-Decision-0.8B/chat_template.jinja
references/Intern-Decision-2B/inference.py
```

These upstream assets and tokenizer files are not included. Obtain them from their original sources, pin/record revisions and hashes, and retain their actual license terms before running conformance checks. See [`INTERN_ADAPTER.md`](../INTERN_ADAPTER.md) for the historical runtime and methodology; its machine-specific paths are examples from that experiment, not a portable environment installer.

`requirements-intern-windows.txt` records the separate historical Windows CUDA environment. It is not the dependency file for CI or the default tests, and it is not installed automatically. Native inference requires reviewed local GGUF paths, compatible CUDA/runtime hardware and explicit model execution; no real checkpoint test is selected by `scripts/check.py`.

## XAAB

Pinned source: [xybern-ai/agent-authz-benchmark, `0249d9ccee4f48a9d49da3363d478715ff99f335`](https://github.com/xybern-ai/agent-authz-benchmark/tree/0249d9ccee4f48a9d49da3363d478715ff99f335). The upstream snapshot carries Apache-2.0 terms. Downloaded `xaab/data/`, source manifests and per-run results are not part of this publication.

The unit tests use inert input dictionaries; they neither read the full corpus nor execute proposed actions. Real evaluation remains an explicit local operation requiring the data, policy, native decision endpoint and the original launcher's bubblewrap/WSL prerequisites. Read [`xaab/README.md`](../xaab/README.md) as a historical protocol record, not evidence that its saved result directories are included in a clean checkout.

## PAGE — separate non-commercial terms

Pinned source: [uber-research/page-benchmark, `42b9bc836c8e8a7ac2b177c2657ed780de4c11ec`](https://github.com/uber-research/page-benchmark/tree/42b9bc836c8e8a7ac2b177c2657ed780de4c11ec).

PAGE's Uber Non-Commercial License addresses the work, derivatives and outputs/results, including attribution for public use. Its upstream notice identifies additional AgentDojo and AgentHarm terms; some AgentHarm material has safety/security-purpose and evaluation-only constraints. A top-level Apache license cannot replace those terms.

The downloaded `page/data/`, original local `page/README.md`, `COMPARISON.*`, run directories and logs stay local and excluded. The data-free parser/metrics tests are not a benchmark run or a new published performance claim. Review upstream licensing before obtaining, using, distributing or publishing any PAGE-derived material.

## Runtime scope

The model catalogue and several trusted launchers retain the original Windows/WSL model paths and process-ownership checks. The Hermes review integration remains single-backend/default-profile and log-only. Source cleanup, unit-test success and a Git repository do not make that runtime portable, enable enforcement, or establish live observation coverage.
