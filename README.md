# Hermes Agent — reproducible host deployment

This repository documents a reproducible [Hermes Agent](https://hermes-agent.nousresearch.com/) deployment for Linux. Hermes runs directly on the host; Docker is used for command execution and the local SearXNG search stack.

The main profile uses model endpoints operated by the deployer. Its primary chat model runs on an OpenAI-compatible Unsloth server, while vision and memory models run on Ollama. An optional researcher profile is the deliberate exception: it uses a cloud model and is isolated from the main profile's Hermes state by a local filesystem-safety patch.

## Data flow

- **Chat**: prompts from the main profile go to the self-operated Unsloth endpoint at `<LLM_HOST>`. This may be a separate machine reached over a tailnet rather than the Hermes host itself.
- **Vision**: image analysis goes to an Ollama server at `<OLLAMA_HOST>`.
- **Memory**: Honcho runs at `127.0.0.1:8100`; its text-generation and embedding requests go to Ollama.
- **Vault search**: QMD indexes the Obsidian vault and runs embedding, query-expansion, and reranking models on the Hermes host through llama.cpp.
- **Files and commands**: file tools run in the host gateway process. Terminal commands use the Docker sandbox backend with explicitly configured mounts for the vault and document cache.
- **Web search**: Hermes sends searches to the local SearXNG service. SearXNG then forwards queries to external search engines, so search terms still leave the host.
- **Messaging**: the SimpleX gateway communicates over the SimpleX network; SimpleX provides end-to-end encryption for message content.

## Models used

| Component | Model | Runtime |
|-----------|-------|---------|
| Primary chat | `unsloth/Qwen3.5-122B-A10B-MTP-GGUF` | Unsloth server at `<LLM_HOST>:8000` |
| Vision | `qwen3-vl:8b-instruct` (Q4_K_M) | Ollama at `<OLLAMA_HOST>:11434` |
| Honcho text generation | `qwen3:8b` (Q4_K_M) | Ollama at `<OLLAMA_HOST>:11434` |
| Honcho embeddings | `qwen3-embedding:0.6b` | Ollama at `<OLLAMA_HOST>:11434` |
| QMD embeddings | `embeddinggemma-300M` (Q8_0) | llama.cpp on the Hermes host |
| QMD query expansion | `qmd-query-expansion-1.7B` (Q4_K_M) | llama.cpp on the Hermes host |
| QMD reranking | `qwen3-reranker-0.6b` (Q8_0) | llama.cpp on the Hermes host |
| Speech-to-text | Whisper `base` | Hermes host |

QMD downloads its model files from Hugging Face; subsequent inference runs on the host.

## Architecture

Hermes and the SimpleX daemon run as systemd user services (`hermes-gateway.service` and `simplex-daemon.service`). The repository's `docker-compose.yml` does not run Hermes: it runs only SearXNG and its Valkey cache. Honcho is a separate Docker deployment described in [`docs/honcho.md`](docs/honcho.md).

Runtime files that contain secrets or machine-specific paths, including `~/.hermes/config.yaml` and `~/.hermes/.env`, are not committed. The templates in this repository use documented `<PLACEHOLDER>` values instead.

## Optional researcher profile

The optional researcher profile uses `deepseek-v4-flash` through `api.deepseek.com`. This model is a personal choice, not a deployment requirement; any frontier model supported by Hermes can be substituted without changing the profile-isolation design. Prompts and any context deliberately included in those prompts are sent to the selected provider. The profile has its own Hermes profile tree, gateway, SimpleX daemon, output directory, and Honcho workspace (`hermes_researcher`).

The `apply_profile_isolation.py` patch lets a named profile use a cloud model without granting its file tools access to the default profile's Hermes state or to sibling profiles. It hard-denies reads and writes to those paths in `agent/file_safety.py`; this restriction is enforced in code rather than through prompting.

The isolation scope is intentionally narrow. The patch still allows the active profile's own tree, the shared Hermes cache, and paths outside the Hermes root, such as an Obsidian vault. It protects Hermes profile state; it is not general host-filesystem isolation. See [`docs/profile-isolation.md`](docs/profile-isolation.md).

## Local patches

`patches/apply-patches.sh` applies four idempotent patches to the Hermes Agent installation:

| Patch | Purpose |
|-------|---------|
| `apply_profile_isolation.py` | Denies a named profile access to default and sibling Hermes profile state — upstream discussion includes [ #10376 / #65693 / #30585](https://github.com/NousResearch/hermes-agent/issues/10376#issuecomment-5162651159). |
| `image_source_output_mounts.py` | Resolves the Docker `/output` mount to its host path so vision can read inbound SimpleX images without an active sandbox session. |
| `simplex_dm_send.py` | Uses SimpleX's structured `/_send` form for outbound direct messages. |
| `simplex_inline_image.py` | Passes inline base64 images from SimpleX mobile clients into the vision pipeline — [#76362](https://github.com/NousResearch/hermes-agent/issues/76362). |

`hermes update` rewrites the installation tree. Re-run `patches/apply-patches.sh` after each update; already-applied patches are skipped.

## Repository layout

| Path | Purpose |
|------|---------|
| `config/config.yaml.example` | Full Hermes configuration template (`_config_version: 33`) |
| `config/hermes.env.example` | Environment template for `~/.hermes/.env` |
| `config/honcho.json.example` | Honcho memory-provider configuration |
| `config/SOUL.md.example` | Agent identity template for `~/.hermes/SOUL.md` |
| `systemd/` | Hermes gateway and SimpleX daemon user units |
| `patches/` | Local Hermes Agent patches and the idempotent patch runner |
| `docker-compose.yml` | Local SearXNG and Valkey services only |
| `searxng-config/` | SearXNG configuration |
| `.env.example` | Environment-variable reference |
| `docs/install.md` | Fresh-install and verification guide |
| `docs/simplex.md` | SimpleX setup and known issues |
| `docs/honcho.md` | Local Honcho deployment |
| `docs/qmd.md` | QMD vault-search setup |
| `docs/profile-isolation.md` | Per-profile filesystem-isolation patch |

## Prerequisites

- Linux with a systemd user session and Python 3.10+
- Docker Engine and Docker Compose v2 for command sandboxes and SearXNG
- An OpenAI-compatible model server, such as Unsloth Studio
- An Ollama server with the vision and Honcho models listed above
- `simplex-chat` CLI if using the SimpleX gateway
- Honcho at `127.0.0.1:8100` if using memory

## Quickstart

1. Install Hermes Agent using the [upstream instructions](https://hermes-agent.nousresearch.com/docs/user-guide/installation) into `~/.hermes/hermes-agent`.
2. Copy `config/config.yaml.example` to `~/.hermes/config.yaml` and replace its `<PLACEHOLDER>` values.
3. Copy `config/hermes.env.example` to `~/.hermes/.env` and fill in its secrets.
4. Start local search with `docker compose up -d`.
5. Copy the required units from `systemd/` to `~/.config/systemd/user/`.
6. Apply the local patches with `patches/apply-patches.sh`.
7. Run `systemctl --user daemon-reload` and enable `hermes-gateway.service`. Enable `simplex-daemon.service` only if using SimpleX.

See [`docs/install.md`](docs/install.md) for model setup, runtime files, verification, and troubleshooting.

## Placeholder reference

| Placeholder | Meaning |
|-------------|---------|
| `<LLM_HOST>` | Primary model server used as `http://<LLM_HOST>:8000/v1` |
| `<LLM_API_KEY>` | API key for the primary model server |
| `<OLLAMA_HOST>` | Ollama server for vision and Honcho (`http://<OLLAMA_HOST>:11434/v1`) |
| `<VAULT_PATH>` | Absolute path to the Obsidian vault, such as `/home/alice/Obsidian/personal` |
| `<HERMES_HOME>` | Hermes home directory (default `~/.hermes`) |
| `<USER_NAME>` | Display name for the Honcho memory peer |
| `<API_SERVER_KEY>` | Key for the local HTTP API (`openssl rand -hex 32`) |
| `<SIMPLEX_CONTACT_DISPLAY_NAME>` | Display name of the primary SimpleX contact |
| `<OBSIDIAN_URL>` / `<OBSIDIAN_API_KEY>` | Optional Obsidian API endpoint and key |

## Notes

- The primary chat model is text-only; `auxiliary.vision` routes image analysis to Ollama.
- The primary model server may require `POST /v1/load {"model_path": "<model>"}` before its first completion.
- Hermes is host-installed. Docker Compose in this repository is only for SearXNG and Valkey.
