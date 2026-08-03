# QMD MCP Server Setup

QMD (`@tobilu/qmd`) is a local, on-device search engine over the Obsidian
vault, served to Hermes as an MCP server over HTTP on port 8181.

## Setup

- **Install**: `npm install -g @tobilu/qmd` (tested with v2.5.3)
- **Config**: `~/.config/qmd/index.yml`

  ```yaml
  collections:
    vault:
      path: <VAULT_PATH>        # e.g. /home/<user>/Obsidian/vault
      pattern: "*.md"
  models:
    embed: hf:ggml-org/embeddinggemma-300M-GGUF/embeddinggemma-300M-Q8_0.gguf
    generate: hf:tobil/qmd-query-expansion-1.7B-gguf/qmd-query-expansion-1.7B-q4_k_m.gguf
    rerank: hf:ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/qwen3-reranker-0.6b-q8_0.gguf
  ```

- **Hermes wiring** — `mcp_servers.qmd` in `~/.hermes/config.yaml`:

  ```yaml
  mcp_servers:
    qmd:
      url: "http://localhost:8181/mcp"
      timeout: 180
  ```

## Service (systemd user unit)

`qmd-mcp.service` — enabled, auto-starts with the user session.

- **PATH fix is required**: the `qmd` bin script is `#!/usr/bin/env node` and
  systemd's default PATH has no nvm — without
  `Environment=PATH=<node-bin-dir>:...` the unit fails with status 127
  (`env: 'node': No such file or directory`).
- **Env (GPU tuning)**: `QMD_RERANK_CONTEXT_SIZE=1024`, `QMD_EMBED_PARALLELISM=2`,
  `LLAMA_LOG_LEVEL=error`. See Issue 2 below.
- Run foreground (no `--daemon`); systemd manages the lifecycle and journal.

| Action | Command |
|--------|---------|
| Status | `systemctl --user status qmd-mcp.service` |
| Restart | `systemctl --user restart qmd-mcp.service` |
| Logs | `journalctl --user -u qmd-mcp.service -f` |
| VRAM check | `nvidia-smi --query-gpu=memory.used,memory.free --format=csv` |

## Issues & fixes

### Issue 1 — Broken `DEFAULT_RERANK_MODEL` (fixed upstream, no action)

v0.9.0 had two divergent `DEFAULT_RERANK_MODEL` constants: `llm.ts` used a
valid `hf:` URI, `store.ts` used an Ollama-style tag. `node-llama-cpp`'s
`parseModelUri` treats non-`hf:`/URL strings as local file paths, so rerank
model resolution failed with `Failed to create any rerank context`.
Upstream aligned the constants (v2.5.3 re-exports `DEFAULT_RERANK_MODEL_URI`).

### Issue 2 — GPU OOM (`ErrorOutOfDeviceMemory`)

- **Symptom**: intermittent Vulkan allocation errors; buffer sizes degrade
  (325 MB → 1 MB) plus secondary `llama_decode: inconsistent sequence
  positions` failures. Errors originate in the host qmd daemon, not the Docker sandbox.
- **Root cause**: VRAM exhaustion on a small-VRAM GPU (e.g. 4 GB): embed model
  (~300 MB) + rerank model (~640 MB) + 1.7B query-expansion model (~1 GB) + up
  to 4 parallel 4096-token rerank contexts (~570-710 MB each). At fault time
  the qmd process held >4 GB. Once full, even 1 MB allocations fail.
- **Fix**: `QMD_RERANK_CONTEXT_SIZE=1024` (4× smaller contexts) +
  `QMD_EMBED_PARALLELISM=2`. Stable at ~1.4-1.8 GB VRAM. If cold-start quality
  regresses on long docs (1024-token truncation), raise to 2048.
- **Escalation**: full CPU mode — `QMD_FORCE_CPU=1` (forces `gpu:false`).
  `QMD_LLAMA_GPU=cpu` is **invalid** — only `metal|vulkan|cuda` are accepted
  (`dist/llm.js`); `cpu` logs a warning and reverts to auto.
  `QMD_LLAMA_GPU=false|off|0` also forces CPU.
- **Notes**: on older GPUs, CUDA is not a reliable escape (prebuilt CUDA
  requires sm_50+ for Maxwell-class parts; node-llama-cpp prebuilds may not
  cover your GPU). Cold start is slow (~80-150 s: model loads + Vulkan shader
  compile); subsequent queries are fast.

### Issue 3 — `llama_decode: inconsistent sequence positions` (secondary)

Seen only while VRAM was exhausted (Issue 2). Disappeared after the memory
fix — downstream symptom, not a separate defect.

## Upgrade caveats

- After `qmd` upgrades, re-check env var semantics (`QMD_RERANK_CONTEXT_SIZE`,
  `QMD_EMBED_PARALLELISM`, GPU override names) against `dist/llm.js`.
- `qmd mcp stop` only kills a manually-started daemon; for the systemd unit use
  `systemctl --user stop qmd-mcp.service`.
