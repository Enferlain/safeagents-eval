# Development

## Supported check environment

The checked configuration is Linux/WSL, Python 3.14, uv 0.13.4, Git and Node.js 24. POSIX ownership checks and `fcntl` are part of the backend contract; this is not a native-Windows backend test suite. Windows CUDA inference is a separate optional runtime.

The project is a source workspace, not an installable Python distribution. `uv.lock` pins the dependency graph, including the exact SafeAgents Git commit. No editable installation of the nested local `trial/upstream` checkout is needed.

## One check command

```sh
UV_PROJECT_ENVIRONMENT=.venv-checks uv run --locked python scripts/check.py
```

A dedicated `.venv-checks` avoids replacing any existing `.venv`, `.venv-trial` or Windows runtime. Initial dependency installation needs network access; the regressions then use only inert fixtures, loopback fixture servers and harmless temporary subprocesses. No benchmark data, credentials, model weights, real agent homes, live proxy or GPU is required.

The runner uses a fresh subprocess for each suite to avoid collisions between the historical `page/runner.py` and `xaab/runner.py` module names. Test subprocesses receive a temporary `HOME`/`HERMES_HOME`; inherited `PYTHONPATH`/`PYTHONHOME` are removed. This is isolation from incidental home lookups, not a claim to sandbox arbitrary hostile tests.

```sh
python scripts/check.py --list
UV_PROJECT_ENVIRONMENT=.venv-checks uv run --locked python scripts/check.py --suite repository
UV_PROJECT_ENVIRONMENT=.venv-checks uv run --locked python scripts/check.py --suite trial --suite ui
```

The default core suite deliberately selects only the data-free Intern calibration, forward/readout and entry-point tests. Dataset/template/compiler conformance is optional:

```sh
UV_PROJECT_ENVIRONMENT=.venv-checks uv run --locked python scripts/check.py --suite references
```

That command fails with a missing-assets explanation unless the separately obtained assets described in [BENCHMARKS.md](BENCHMARKS.md) are present. It does not run the CUDA checkpoint test. Do not substitute unqualified root test discovery on a machine where optional integrations might be available and assume it is the same default check plan.

## CI

`.github/workflows/ci.yml` runs the same locked command on Ubuntu with Python 3.14 and Node 24. Actions are commit-pinned, permissions are read-only, checkout credentials are not persisted, and no repository secrets are required. CI validates source/fixture behavior, not live Hermes routing, model quality, permissions enforcement or GPU inference.

Running the command locally exercises the job's check payload. It is not evidence that a hosted GitHub Actions run has occurred.

## Publication and changes

- `.gitignore` is a source allowlist, not just a few private-directory exclusions. Review new paths/extensions deliberately.
- Do not force-add ignored files. Private captures, journals, free-text review notes, generated logs, weights, environments and downloaded benchmark bundles stay local.
- Keep runtime/model provenance and ownership checks intact. Machine-specific launch paths are documented limitations, not strings to replace blindly during formatting cleanup.
- Keep SafeAgents log-only unless an enforcement change is separately reviewed and approved. Tests and retrospective labels do not grant execution permission.
- Do not run benchmark launchers, restart services, deploy plugin files or inspect live private captures merely to validate a source change.
- Inspect the staged filename list and diff before a first commit. A conservative ignore policy and pattern scan reduce accidental disclosure; neither guarantees that every future change is safe to publish.
- Retain third-party license/attribution boundaries. PAGE data and its historical outputs are specifically excluded; see [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
