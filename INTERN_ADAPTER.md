# Intern-Decision GGUF adapter

## Scope

`intern_adapter.py` implements the existing benchmark's **same one-question noul payload** for the original Intern-Decision 0.8B and 2B Q8 GGUF files. It does not modify/reconvert GGUFs, execute proposed tool calls, call a chat endpoint, sample tokens, or generate text.

## Encoding and readout

1. Compile state/schema/messages following the pinned released `internlm/.../inference.py` implementations. The state and benchmark question are unchanged; Intern-specific schema wrapping is required by the checkpoint.
2. Render the released `chat_template.jinja` with `add_generation_prompt=False`, `enable_thinking=False`, `add_vision_id=True`. This includes the complete assistant JSON skeleton with its trained `<decision>` marker and closing suffix.
3. Tokenize with GGUF vocabulary, no added BOS, parsing trained special tokens. Fail on overflow or an unexpected marker count; never truncate.
4. Clear recurrent/KV state for each row. Feed the **entire** prefix to one `llama_decode` call, with output logits requested only at `marker_index - 1`. The complete skeleton is still evaluated, not replaced with a generated answer or a shortened prompt.
5. Read A and B token logits only. A means no/allow; B means yes/block. Compute `softmax([logit_A, logit_B] / T)[1]`.
6. Preserve released calibration: **0.8B T=2.747760550703**, **2B T=2.100509348278**. No extra sampling/temperature layer.

The implementation uses `llama-cpp-python`'s model/context/batch wrappers directly. It avoids the high-level generation class and avoids an 8192-by-vocabulary host logits array. Runtime still reserves GPU compute buffers for batch/microbatch 8192.

## Runtime

- Source/tests/results: `/home/imi/Projects/safeagents-eval`.
- Isolated Windows runtime: `D:\Projects\safeagents-eval-runtime\.venv`.
- Project-specific Windows package cache: `D:\Projects\safeagents-eval-runtime\uv-cache`.
- Python 3.12.10; `llama-cpp-python==0.3.36` official CUDA 13.2 wheel.
- Bundled llama.cpp revision: `0c1e57098bba43ac29e6e3b677cdceebdd22334f`.
- Wheel: https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.36-cu132/llama_cpp_python-0.3.36-py3-none-win_amd64.whl
- CUDA runtime DLLs `cudart64_13.dll`, `cublas64_13.dll`, `cublasLt64_13.dll` were copied into this isolated environment from the existing native binary directory. The wheel's llama/ggml DLLs were **not** replaced: the existing server's ABI differs.
- Process-local `CUDA_VISIBLE_DEVICES=GPU-4cec666b-991c-fe31-e63f-6455bc828119` selects the 3090 before importing the binding. Logs confirm the single visible RTX 3090 and 25/25 layers offloaded for each checkpoint.
- Context, batch and microbatch are **8192**; values read back from the live context are asserted. Causal attention, flash attention enabled, one sequence.
- Windows uv cannot lock the WSL UNC filesystem. A failed installation attempt left an unused `.venv-win` directory and `.uv-cache-win` stub in the WSL workspace; the functioning runtime is the D: path above. No global Python packages were changed.

## Run

From the WSL workspace:

```bash
.venv/bin/python -m unittest -v test_intern test_metrics test_reporting
/mnt/d/Projects/safeagents-eval-runtime/.venv/Scripts/python.exe -m unittest -v test_intern test_metrics test_reporting
.venv/bin/python run.py run --model intern-decision
.venv/bin/python analyze.py
```

The runner refuses to overwrite existing Intern `predictions.jsonl` files. Preserve existing result directories before an intentional rerun. Each checkpoint runs in its own Windows process and releases its context/model before exit.

## Validation and results

- All 562 message compilations match the pinned upstream compiler for both checkpoints.
- Every compiled token sequence matches the released Hugging Face tokenizer, with no truncation. `encoding-validation.json` records lengths and IDs.
- CUDA smoke test repeats a real dataset row and checks probability reproducibility, zero generated tokens, candidate IDs and live 8192 batch/microbatch.
- Independent readout validation uses seven real rows per checkpoint: original run logits reproduce, and packed sparse output agrees with explicit `llama_get_logits_ith` batch-index selection.
- A separate causal shortened-prefix cross-check records numerical differences rather than substituting that path. Maximum sampled P(block) differences: 0.010004 (0.8B), 0.004478 (2B). No exact numerical equivalence is claimed.
- Full 562-row runs completed for each checkpoint with zero errors. Their observed results are weak, especially at the fixed 0.5 threshold; see `REPORT.md` and `summary.json`. This is not a reason to relabel probabilities or change calibration.
- **Not validated:** parity against upstream BF16 Transformers logits/weights. Encoding and llama.cpp readout consistency do not prove BF16 parity or conversion fidelity.
- Model size, modification time and SHA256 match before and after each run. Original SHA256s:
  - 0.8B: `3b3df57b50bfe695f31b0d8fb9ad9dbb7a4b1e08134d1140051a32a585e54f9e`
  - 2B: `dbb4674c44de5aa66f78261a7e4e7f539d32ebde1af879d5ed6c2dc35da55e32`

## Timing

Intern results are tagged `llama-cpp-python-intern-full-prefix`, separately from native `native-systemone-http` results in the report/summary. `seconds` is in-process payload encoding, full-prefix forward and readout. `forward_seconds` is state reset, batch setup, decode and synchronized candidate readout. Startup, validation and one warm-up are excluded. Native HTTP timings include a different transport and backend revision and are **not directly interchangeable**.

## Artifacts

`results/intern-decision-*/` holds raw predictions and A/B logits, temperatures, token counts/hashes, runtime/source hashes, encoding/readout validation, logs, exact launch command, fingerprints, calibration analysis and threshold curves. `references/sources.json` pins upstream revisions; reference model weights were not downloaded. `requirements-intern-windows.txt` records the isolated runtime packages.
