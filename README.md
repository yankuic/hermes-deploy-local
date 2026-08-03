# Hermes Agent — reproducible host deployment

A reproducible, copy-and-adapt setup for running [Hermes Agent](https://hermes-agent.nousresearch.com/) (by Nous Research) as a **host install** with:

- a custom OpenAI-compatible LLM endpoint (e.g. Unsloth Studio) as primary model
- a local Ollama fallback model + vision model (`qwen3-vl`)
- a Docker sandboxed terminal backend with a persistent Obsidian vault mount
- SimpleX chat gateway (systemd daemon + gateway units)
- honcho memory provider, QMD MCP over the Obsidian vault, optional researcher profile

Everything personal has been scrubbed: no API keys, no machine paths, no IPs, no
usernames. All machine-specific values are `<PLACEHOLDER>`s documented below.

## Repository layout

| Path | Purpose |
|------|---------|
| `config/config.yaml.example` | Full Hermes config template (`_config_version: 33`) |
| `config/hermes.env.example` | Environment template for `~/.hermes/.env` |
| `config/honcho.json.example` | honcho memory provider config |
| `config/SOUL.md.example` | Agent identity file (`~/.hermes/SOUL.md`) |
| `systemd/hermes-gateway.service` | Gateway user unit (uses `%h`, no hardcoded paths) |
| `systemd/simplex-daemon.service` | SimpleX daemon user unit |
| `patches/` | Local hermes-agent patches + idempotent `apply-patches.sh` |
| `.env.example` | Full environment variable reference |
| `docs/install.md` | Step-by-step fresh-install guide |
| `docs/simplex.md` | SimpleX gateway setup + known issues |
| `docs/honcho.md` | Memory provider setup |
| `docs/qmd.md` | QMD MCP (vault search) setup |
| `docs/profile-isolation.md` | Optional per-profile filesystem isolation |

## Prerequisites

- Linux host with `systemd --user`, Docker (sandbox backend), and Python 3.10+
- A running OpenAI-compatible LLM server (see `docs/install.md` for the load endpoint contract)
- A LAN (or local) Ollama host for the fallback/vision model
- `simplex-chat` CLI for the SimpleX gateway (optional)
- honcho server at `127.0.0.1:8100` for memory (optional)

## Quickstart

1. Install hermes-agent per the [upstream docs](https://hermes-agent.nousresearch.com/docs/user-guide/installation) into `~/.hermes/hermes-agent`.
2. `cp config/config.yaml.example ~/.hermes/config.yaml` and replace the `<PLACEHOLDER>` values (table below).
3. `cp config/hermes.env.example ~/.hermes/.env` and fill in secrets.
4. Install the systemd units: `cp systemd/*.service ~/.config/systemd/user/`.
5. Apply the local patches: `patches/apply-patches.sh`.
6. `systemctl --user daemon-reload && systemctl --user enable --now simplex-daemon.service hermes-gateway.service`.

Full details, verification checks, and troubleshooting: **`docs/install.md`**.

## Placeholder reference

| Placeholder | Meaning |
|-------------|---------|
| `<LLM_HOST>` | Primary LLM server host — used as `http://<LLM_HOST>:8000/v1` |
| `<LLM_API_KEY>` | API key for the primary LLM server |
| `<OLLAMA_HOST>` | Ollama host for fallback model + vision (`http://<OLLAMA_HOST>:11434/v1`) |
| `<VAULT_PATH>` | Absolute path to the Obsidian vault, e.g. `/home/alice/Obsidian/personal` |
| `<HERMES_HOME>` | Hermes home dir (default `~/.hermes`) |
| `<USER_NAME>` | Display name for the honcho memory peer |
| `<API_SERVER_KEY>` | Key for the local HTTP API (`openssl rand -hex 32`) |
| `<SIMPLEX_CONTACT_DISPLAY_NAME>` | Display name of the primary SimpleX contact |
| `<OBSIDIAN_URL>` / `<OBSIDIAN_API_KEY>` | Optional Obsidian API endpoint + key |

## Notes

- **Patches re-apply after every `hermes update`** — run `patches/apply-patches.sh` after each update (it is idempotent and skips already-applied patches).
- The `docker-compose.yml` is **not** used: this is a host install.
- Main model is text-only; `auxiliary.vision` routes image analysis to the Ollama vision model.
- The primary LLM endpoint may require `POST /v1/load {"model_path": "<model>"}` before the first completion.

## License

MIT — see `LICENSE`. Hermes Agent itself is a separate project by Nous Research.
