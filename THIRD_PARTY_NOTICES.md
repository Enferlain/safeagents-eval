# Third-party notices and license boundaries

Apache-2.0 applies to this project's original contributions. Third-party material retains its upstream terms. Downloaded benchmark datasets, model weights, local observations and generated evaluation artifacts are not included. This source tree contains a separately licensed non-commercial PAGE adaptation and must **not** be represented as an Apache-only distribution.

## Component map

| Component | Scope and terms |
| --- | --- |
| Original runners, observation/replay code, Hermes integration, tests and docs | Apache-2.0, except material specifically identified below |
| `intern_adapter.py` compiler/template adaptation | Apache-2.0; preserve the upstream Intern and Qwen notices below |
| **`page/runner.py`** | **Uber Non-Commercial License**; local binary-prompt adaptation, not part of the Apache grant |
| Downloaded PAGE data and historical PAGE results/docs | Not included; non-commercial and underlying-data restrictions still govern local use/publication |
| SafeAgents and other installed Python dependencies | Separate upstream licenses; installed from the locked dependency graph, not vendored here |

## Intern-Decision and Qwen

The compiler/template conventions used by `intern_adapter.py` follow the released Intern-Decision implementation. This is a local text-only binary decision/readout adaptation, not an unmodified upstream inference program. Local adaptation/modification notices were added on **2026-11-11**.

Exact upstream references are recorded in [`references/sources.json`](references/sources.json):

- [internlm/Intern-Decision-0.8B](https://huggingface.co/internlm/Intern-Decision-0.8B/tree/42457fafd13d10d0b0d9be46cb30a23caa409994), revision `42457fafd13d10d0b0d9be46cb30a23caa409994`.
- [internlm/Intern-Decision-2B](https://huggingface.co/internlm/Intern-Decision-2B/tree/85c5657708a09884ff9fd8d7f57789b410a97b33), revision `85c5657708a09884ff9fd8d7f57789b410a97b33`.

Both pinned repositories' `LICENSE` and `LICENSE-QWEN` carry Apache-2.0 terms. Their notices include:

> Copyright 2026-2027 Shanghai AI Laboratory
>
> Copyright 2026 Alibaba Cloud.

Verbatim retained texts are under [`licenses/intern/`](licenses/intern/), one pair per pinned checkpoint. Source URLs and hashes are in [`licenses/SOURCES.json`](licenses/SOURCES.json). Downloaded `inference.py`, templates, tokenizer files and model weights themselves are not included in this publication.

## PAGE

Source: [uber-research/page-benchmark](https://github.com/uber-research/page-benchmark/tree/42b9bc836c8e8a7ac2b177c2657ed780de4c11ec), revision `42b9bc836c8e8a7ac2b177c2657ed780de4c11ec`.

> Copyright 2026 Uber

`page/runner.py` preserves/adapts PAGE baseline persona-description and benign/adversarial/out-of-domain definitions into a native binary `noul` prompt. It is **not the original PAGE baseline**. To avoid claiming independent Apache ownership of that prompt, the complete adapted module is distributed under the retained [Uber Non-Commercial License](licenses/page/LICENSE.txt). Local adaptation/modification notices were added on **2026-11-11**. See the verbatim [upstream notice](licenses/page/NOTICE.txt).

The upstream license addresses use, derivatives, **outputs/results**, distribution notices and attribution for public use. Do not use the Apache license of other files to override these requirements. PAGE data, result-bearing local `page/README.md`, `COMPARISON.*`, scored runs and logs are intentionally excluded from Git.

The upstream notice also identifies:

- [AgentDojo](https://github.com/ethz-spylab/agentdojo), whose corresponding material remains subject to its upstream license/attribution.
- [AgentHarm](https://huggingface.co/datasets/ai-safety-institute/AgentHarm), pinned upstream revision `e23b3fe60a0da9037314b88e5ee3a0c054970dad`, with an additional safety/security-purpose clause and a no-training instruction.

Those underlying datasets are not included. Review their original terms before separately obtaining or using them. The data-free PAGE unit tests are not a new benchmark-performance result.

## XAAB

Source: [xybern-ai/agent-authz-benchmark](https://github.com/xybern-ai/agent-authz-benchmark/tree/0249d9ccee4f48a9d49da3363d478715ff99f335), revision `0249d9ccee4f48a9d49da3363d478715ff99f335` (Apache-2.0 in the local pinned snapshot). The local classifier-only adaptation is not an official XAAB reference-pack result. Downloaded scenario YAML, source payloads and generated run artifacts are not bundled.

## SafeAgents and its judge benchmark

- [huggingface/safeagents](https://github.com/huggingface/safeagents/tree/2a178aacb74fe386667956d825fed49453823f70) is a separate Apache-2.0 dependency, pinned to `2a178aacb74fe386667956d825fed49453823f70` by `pyproject.toml` and `uv.lock`. The local nested checkout is excluded.
- The historical [harness-hub/safeagents-judge-bench](https://huggingface.co/datasets/harness-hub/safeagents-judge-bench) snapshot is described by `dataset-provenance.json`. Its current upstream card declares Apache-2.0; the original local snapshot revision was not recorded. Neither its payload nor generated model outputs are bundled. `REPORT.md` is the reviewed local methodology/aggregate report, not a copy of the current upstream leaderboard.

No upstream endorsement, compatibility guarantee, benchmark parity or safe enforcement claim is implied.
