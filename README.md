# safeagents-eval

Local decision-model evaluation and experimental **log-only** SafeAgents observation tooling, including a Hermes desktop review plugin.

This is an independent experiment workspace, not the upstream [SafeAgents](https://github.com/huggingface/safeagents) project. It evaluates frozen proposed actions/messages; benchmark actions are **not executed**. Passing classifier tests is not evidence that an agent is protected or that enforcement is enabled.

## What is here

| Path | Purpose |
| --- | --- |
| `run.py`, `analyze.py`, `REPORT.md` | Historical SafeAgents judge-benchmark runner, metrics and report |
| `intern_adapter.py`, `intern_runner.py` | Optional Intern decision-model adapter; native runtime is separate |
| `trial/` | Judge adapter, private observation/replay tooling and regression tests |
| `trial/security-plugin/` | Experimental Hermes backend API, metadata hooks and desktop pane |
| `xaab/` | Classifier-only XAAB input adaptation and analysis |
| `page/` | Optional PAGE adaptation; see the separate license boundary below |
| `scripts/check.py`, `tests/` | Data-free regression entry point and publication-boundary tests |
| `docs/` | Development and benchmark prerequisites |

The current decision-model catalogue supports explicit **D3 Lite** (`lite`) and **QEV** (`qev`) choices. Live-judge identity and offline replay selection are separate. Model weights are not included, and selecting a replay model does not change the live judge.

## Run the checks

Requirements: **Linux/WSL, Python 3.14, uv, Git and Node.js 24+**. Dependency setup downloads pinned packages and the pinned SafeAgents source; test execution itself needs no model, credentials, benchmark download or external API. Some tests create loopback-only fixture HTTP servers and harmless temporary subprocesses.

```sh
# Use a dedicated development environment; do not replace existing benchmark venvs.
UV_PROJECT_ENVIRONMENT=.venv-checks uv run --locked python scripts/check.py
```

The same command runs in CI. It runs core metrics/adapter unit tests, all trial regressions, PAGE/XAAB unit tests, repository publication checks and the Node UI tests. It gives subprocesses a temporary agent home and removes inherited Python import-path overrides. It never invokes a benchmark launcher or the native CUDA checkpoint test.

To inspect or narrow the plan:

```sh
python scripts/check.py --list
UV_PROJECT_ENVIRONMENT=.venv-checks uv run --locked python scripts/check.py --suite trial
```

Reference-compiler conformance is an explicit optional suite requiring separately obtained public assets. Native/CUDA integration is not part of this entry point. See [development](docs/DEVELOPMENT.md) and [benchmark prerequisites](docs/BENCHMARKS.md).

## Runtime scope and limitations

- Observation remains **log-only**; contextual pre-execution enforcement is not implemented by this plugin.
- The provisional `0.88` cutoff is not validated calibration for either model.
- The Hermes integration targets a **single backend/default profile**. Public ownership checks are conservative guards, not a general cross-backend transport guarantee.
- Some model launchers and catalogue paths are intentionally tied to the original Windows/WSL machine. This cleanup does not make them portable or silently rewrite those trust/ownership checks.
- A proxy being online does not prove that a session is covered. Unproxied routes, including `openai-codex`, are not watched.
- Private captures may contain credentials and task text. They are owner-only plaintext, not encrypted. Do not publish them or attach them to issues.
- Installing Python backend files does not hot-reload an already running Hermes backend. Runtime activation and live verification are separate from source tests.

Operational details and limitations are in [observation](trial/OBSERVATION.md) and the [security integration](trial/SECURITY_APP.md). Those documents include historical, machine-specific deployment records; their sample paths are not a portable installation recipe.

## Publication boundary

Git uses an explicit allowlist. Virtual environments, caches, model weights, downloaded third-party assets, generated results/logs, private observations, journals, old backups and the nested upstream checkout remain **local and untracked**. Nothing is deleted by these exclusions. New publishable file types/directories need an explicit review of `.gitignore`.

The historical benchmark snapshot is recorded by `dataset-provenance.json`; it is not bundled. Fetching the latest upstream dataset is not an exact reproduction of the historical report. No setup or test command silently downloads/replaces that snapshot.

## License and third-party material

The original project code is offered under **Apache-2.0**; see [LICENSE](LICENSE), [NOTICE](NOTICE) and [third-party notices](THIRD_PARTY_NOTICES.md).

**`page/runner.py` is a separately licensed, non-commercial PAGE prompt adaptation**, not part of the Apache grant. Its upstream terms also address outputs/results, with additional terms for some underlying data. The applicable license and notice are retained under `licenses/page/`; downloaded PAGE data and historical PAGE results are excluded. Consequently, this mixed source tree is not an Apache-only distribution. Review [third-party notices](THIRD_PARTY_NOTICES.md) before use or redistribution; the repository-level license does not relicense third-party material.
