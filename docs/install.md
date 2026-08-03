# Fresh Install Guide — Hermes Agent (host)

Step-by-step reproduction of a complete Hermes Agent host deployment. It
assumes nothing about the host except the prerequisites below. Every
machine-specific value comes from the `<PLACEHOLDER>` table — resolve them
once, then this guide is mechanical.

## Prerequisites

- Linux host, systemd user session, Docker Engine (sandbox backend)
- Hermes Agent installed at `~/.hermes/hermes-agent` (see upstream install docs; `hermes` CLI resolves, `~/.hermes/bin` contains tirith/uv/uvx)
- An OpenAI-compatible LLM server (primary model) — e.g. Unsloth Studio
- An Ollama host on the LAN (fallback model + vision model)
- `simplex-chat` CLI (only if you use the SimpleX gateway)
- honcho server at `127.0.0.1:8100` (only if you use memory)

## Variables

| Placeholder | Meaning |
|-------------|---------|
| `<LLM_HOST>` | Primary LLM server host (`http://<LLM_HOST>:8000/v1`) |
| `<LLM_API_KEY>` | API key for the primary LLM server |
| `<OLLAMA_HOST>` | Ollama host (`http://<OLLAMA_HOST>:11434/v1`) |
| `<VAULT_PATH>` | Absolute path to the Obsidian vault |
| `<HERMES_HOME>` | Hermes home (`~/.hermes` by default) |
| `<USER_NAME>` | Your display name (honcho peer) |
| `<API_SERVER_KEY>` | Local API key (`openssl rand -hex 32`) |
| `<SIMPLEX_CONTACT_DISPLAY_NAME>` | Primary SimpleX contact display name |
| `<OBSIDIAN_URL>` / `<OBSIDIAN_API_KEY>` | Optional Obsidian API |

## Step 1 — Install hermes-agent

Follow the upstream installation guide. Verify:

```bash
~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --version
```

## Step 2 — Write `~/.hermes/config.yaml`

```bash
cp config/config.yaml.example ~/.hermes/config.yaml
# replace every <PLACEHOLDER> from the table above
```

Key sections (see the template for the full document):

- `model` — primary LLM (provider `custom:unsloth`, `http://<LLM_HOST>:8000/v1`)
- `custom_providers` — named providers with `context_length` metadata; `model.default` must be a key under the selected provider's `models` dict
- `auxiliary.vision` — vision model on Ollama (the main model is text-only)
- `terminal` — docker sandbox; `docker_volumes` mounts the vault and `<HERMES_HOME>/cache/documents:/output`
- `memory.provider: honcho` — requires `honcho.json` (Step 8)
- `platforms.simplex.enabled: true` — requires the daemon (docs/simplex.md)

## Step 3 — Write `~/.hermes/.env`

```bash
cp config/hermes.env.example ~/.hermes/.env
# fill in <API_SERVER_KEY>, <SIMPLEX_CONTACT_DISPLAY_NAME>, optional Obsidian vars
```

Reload with `hermes config reload` or restart the gateway after editing.
Note: each variable must be on its own line.

## Step 4 — Systemd user units

```bash
mkdir -p ~/.config/systemd/user
cp systemd/hermes-gateway.service ~/.config/systemd/user/
cp systemd/simplex-daemon.service ~/.config/systemd/user/   # only if using SimpleX
systemctl --user daemon-reload
systemctl --user enable --now simplex-daemon.service
systemctl --user enable --now hermes-gateway.service
systemctl --user is-active simplex-daemon hermes-gateway
```

The units use `%h` (home dir), so they work for any user. If you don't use
nvm, adjust the node path in `Environment="PATH=..."`.

## Step 5 — SimpleX prerequisites

See `docs/simplex.md`. In short:

```bash
curl -L https://github.com/simplex-chat/simplex-chat/releases/latest/download/simplex-chat-ubuntu-22_04-x86_64 -o ~/bin/simplex-chat
chmod +x ~/bin/simplex-chat
mkdir -p ~/.hermes/cache/documents/.simplex-tmp
# run interactively once: /create bot [files=on] <name> <bio>, /a (address), /ac <id> (accept), /q
```

## Step 6 — LLM + vision endpoints

- The primary endpoint may require loading the model first:

  ```bash
  curl -X POST http://<LLM_HOST>:8000/v1/load -H "Authorization: Bearer <LLM_API_KEY>" \
    -d '{"model_path":"<MODEL_NAME>"}'
  ```

  (Without it, completions fail with "No model loaded".)

- Pull the vision model on the Ollama host:

  ```bash
  curl -s http://<OLLAMA_HOST>:11434/api/pull -d '{"name":"qwen3-vl:8b-instruct","stream":false}'
  ```

- Verify vision by sending solid red/green/blue PNGs to
  `http://<OLLAMA_HOST>:11434/v1/chat/completions`; each must answer the correct color.

## Step 7 — Apply local patches

```bash
patches/apply-patches.sh
```

These fix three upstream quirks (see `patches/README.md`). **Re-run after
every `hermes update`** — the updater rewrites the install tree.

## Step 8 — Runtime files

```bash
cp config/SOUL.md.example ~/.hermes/SOUL.md          # agent identity
cp config/honcho.json.example ~/.hermes/honcho.json  # memory provider (replace <USER_NAME>)
```

Optional desktop launcher (the `.desktop` file must exec the wrapper script,
because GNOME launches apps with a minimal PATH):

`~/.local/bin/hermes-desktop` (chmod +x):
```bash
#!/usr/bin/env bash
export PATH="$HOME/.nvm/versions/node/v24.11.1/bin:$HOME/.local/bin:$PATH"
exec "$HOME/.local/bin/hermes" desktop
```

`~/.local/share/applications/hermes.desktop`:
```ini
[Desktop Entry]
Name=Hermes Dashboard
Comment=Hermes Agent Desktop
Exec=/home/<user>/.local/bin/hermes-desktop
Icon=/home/<user>/Pictures/icons/hermes-agent-64x64.png
Terminal=false
Type=Application
Categories=Utility;
```

Validate: `desktop-file-validate ~/.local/share/applications/hermes.desktop`.

## Step 9 — Verification checklist

- [ ] `systemctl --user is-active hermes-gateway.service` → active
- [ ] `systemctl --user is-active simplex-daemon.service` → active
- [ ] `~/.hermes/logs/gateway.log`: "✓ simplex connected", "Gateway running with N platform(s)"
- [ ] Main model answers via the primary endpoint (no 401, no "No model loaded")
- [ ] Vision: `vision_analyze` on `/output/test.jpg` returns `"success": true`
- [ ] Inline image from the SimpleX mobile app → transcription returned
- [ ] Outbound SimpleX DM delivered (no `contactNotFound`)
- [ ] Files received over SimpleX land in `~/.hermes/cache/documents/` and are readable at `/output/` in the sandbox
- [ ] `hermes desktop` launches (via wrapper)

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| 401 from the primary LLM | API key rotated — update `model.api_key` and `custom_providers.*.api_key` (keep them in sync) |
| "No model loaded" | Missing `POST /v1/load` (Step 6) |
| Vision "not reachable inside the sandbox" | `image_source` patch missing after an update — re-run `patches/apply-patches.sh` |
| SimpleX images "I don't see any image" | `simplex_inline_image` patch missing — re-apply |
| SimpleX DMs silently dropped | `simplex_dm_send` patch missing — re-apply (see docs/simplex.md) |
