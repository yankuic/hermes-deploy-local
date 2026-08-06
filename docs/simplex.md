# SimpleX Gateway Setup

Connecting [SimpleX Chat](https://simplex.chat/) as a messaging platform for the Hermes gateway — including multi-profile (second gateway) setup.

## Architecture

```
SimpleX App (phone) ←→ SimpleX Relay Network ←→ simplex-chat daemon ←[WS 5225]→ Hermes Gateway → LLM
```

The `simplex-chat` CLI runs as a WebSocket server on `127.0.0.1:5225`. The Hermes simplex plugin adapter connects to it, receives messages, sends them to the agent, and relays replies back through the daemon.

## Prerequisites

- `simplex-chat` CLI binary (tested with v6.5.6.1): `~/bin/simplex-chat`
- `websockets` package in the Hermes venv
- A SimpleX contact already connected to the bot's profile

## Install the daemon

```bash
curl -L https://github.com/simplex-chat/simplex-chat/releases/latest/download/simplex-chat-ubuntu-22_04-x86_64 -o ~/bin/simplex-chat
chmod +x ~/bin/simplex-chat
mkdir -p ~/.hermes/cache/documents/.simplex-tmp
```

Run interactively once to create a profile and connect contacts: `/create bot [files=on] <name> <bio>`, `/a` (simpleX address), `/ac <id>` (accept request), `/q` (quit). The chat DB persists at `~/.local/share/simplex-chat/simplex_v1_chat.db`.

Systemd unit: `systemd/simplex-daemon@.service` (in this repo) — generic template backed by `scripts/simplex-daemon-run`, which maps the profile to port / db / display name / cache folders. For the default (root) install on port 5225 with file auto-accept up to 50 MB:

```bash
systemctl --user daemon-reload
systemctl --user enable --now simplex-daemon@default.service
```

## Hermes configuration

`~/.hermes/.env`:

```env
SIMPLEX_WS_URL=ws://127.0.0.1:5225
SIMPLEX_HOME_CHANNEL=<SIMPLEX_CONTACT_DISPLAY_NAME>   # contact display name or ID
SIMPLEX_ALLOW_ALL_USERS=true                            # allow all contacts (or SIMPLEX_ALLOWED_USERS)
SIMPLEX_AUTO_ACCEPT=true                                # auto-accept new contact requests
```

Each variable on its own line — two values on one line silently breaks parsing.

`~/.hermes/config.yaml`:

```yaml
platforms:
  simplex:
    enabled: true
```

## Authorization

| Method | How |
|---|---|
| Allow-all | `SIMPLEX_ALLOW_ALL_USERS=true` |
| Allowlist | `SIMPLEX_ALLOWED_USERS=4,5` (numeric contact IDs or display names) |
| Pairing | Message the bot → reply code → `hermes pairing approve simplex <CODE>` |

## Known issues (patches in `patches/`)

### 1. DM addressing: bare `@<id>` is a display-name lookup

`simplex-chat` parses bare `@<value>` as a *display-name* lookup. Outbound DMs
like `@4 Hello` resolve to `contactNotFound` and are silently dropped
(fire-and-forget WS send — no error anywhere). The structured command
addresses by numeric contact ID:

| Format | Works? |
|---|---|
| `@4 Hello` | ❌ `contactNotFound` |
| `/_send @4 json [{"msgContent":{"type":"text","text":"Hello"}}]` | ✅ |

**Fix**: `patches/simplex_dm_send.py` rewrites both `send()` and
`_standalone_send()` to the structured form. Re-apply after every `hermes update`.

Reproduction:

```python
import asyncio, json, websockets

async def test():
    ws = await websockets.connect('ws://127.0.0.1:5225')
    await ws.send(json.dumps({'corrId': 't1', 'cmd': '@4 Hello'}))
    print('@4:', (await ws.recv())[:200])          # chatCmdError / contactNotFound
    composed = json.dumps([{'msgContent': {'type': 'text', 'text': 'Hello'}}])
    await ws.send(json.dumps({'corrId': 't2', 'cmd': f'/_send @4 json {composed}'}))
    print('/_send:', (await ws.recv())[:200])      # newChatItems
    await ws.close()

asyncio.run(test())
```

### 2. Inline (base64) images from mobile clients

SimpleX mobile clients deliver photos as inline base64 (`msgContent.type="image"`,
`msgContent.image="data:image/jpg;base64,..."`) with **no** `chatItem.file`
field. The stock adapter drops the pixels, so the agent sees only the caption
(or nothing).

**Fix**: `patches/simplex_inline_image.py` extracts the data URL into
`media_urls`, and the gateway's vision pipeline (`image_source._resolve_data_url`
→ vision model) analyzes it. Re-apply after every `hermes update`.

### 3. Vision reads through `/output`

The gateway mounts `<HERMES_HOME>/cache/documents:/output` (docker sandbox
`/output`), which is **not** part of the auto-mounted cache list — model-supplied
`/output/x.jpg` reads fail when no sandbox session is active (often the case:
`vision_analyze` is the model's first tool call of a turn).

**Fix**: `patches/image_source_output_mounts.py` maps configured docker volumes
back to host paths before the cache-root check. Re-apply after every `hermes update`.

## Logs

```bash
journalctl --user -u hermes-gateway -f
journalctl --user -u simplex-daemon -f   # minimal output by design
```

`Sending response` in gateway logs but nothing arrives → daemon's relay
connection may be down (`ss -tpn | grep simplex-chat`).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `cannot reach daemon at ws://127.0.0.1:5225` | Daemon not running |
| `contactNotFound` for numeric ID | Bare `@<id>` used — `/_send` patch missing |
| `Unauthorized user` in logs | Contact not allowed and allow-all is off |
| Gateway sends but phone receives nothing | Daemon relay connection down |

## Multi-profile: a second gateway (e.g. `researcher`)

A named Hermes profile (`HERMES_HOME=~/.hermes/profiles/<name>`) gets its own
simplex daemon on a second port with its own identity DB, plus its own gateway
instance of the generic `@` templates. Full walkthrough: `docs/profiles.md`.

1. **Enable the generic units for the profile** — `scripts/simplex-daemon-run`
   already maps `researcher` (5226) and `jseeker` (5227) to ports, identity DBs,
   display names and cache folders. For a new profile add a case there (or set
   `SIMPLEX_PORT` / `SIMPLEX_DISPLAY_NAME`), then:

   ```bash
   systemctl --user enable --now 'simplex-daemon@researcher.service'
   systemctl --user enable --now 'hermes-gateway@researcher.service'
   ```

   `simplex-daemon-run` launches
   `simplex-chat -p 5226 -d ~/.hermes/profiles/<name>/simplex_db --create-bot-display-name "<Bot Name>" -y --files-folder ~/.hermes/cache/documents --temp-folder ~/.hermes/cache/documents/.simplex-tmp-<name> --auto-accept-files 50000000`.

   Flags that matter: `-d` (separate identity DB — a fresh DB prompts for a
   display name on stdin and dies under systemd, hence
   `--create-bot-display-name`), `-y` (auto-confirm migrations), separate
   `--temp-folder` per daemon.

2. **Profile config** — `~/.hermes/profiles/<name>/config.yaml`:

   ```yaml
   platforms:
     simplex:
       enabled: true
       extra:
         ws_url: ws://127.0.0.1:5226
     api_server:
       port: 8643    # required — the primary gateway binds 8642
   ```

3. **Profile env** — `~/.hermes/profiles/<name>/.env`:
   `SIMPLEX_WS_URL=ws://127.0.0.1:5226` (plus the same SIMPLEX_* vars as above).

Gotchas:

- **Relay pinning**: the bot's auto-created address pins to whatever relay the
  daemon picks at creation. If the phone shows "connecting…" forever while the
  host side is healthy (daemon holds an ESTAB connection, address reachable),
  the relay is failing one-way delivery. Workaround: create a one-time invite
  from the app (a different relay) and `/connect <link>` via the daemon WS probe:

  ```python
  import asyncio, json, websockets
  async def main():
      async with websockets.connect('ws://127.0.0.1:5226') as ws:
          await ws.send(json.dumps({'corrId': 'x', 'cmd': '/address'}))
          async for raw in ws:
              print(raw); return
  asyncio.run(main())
  ```

- **api_server port conflict**: a second gateway must set
  `platforms.api_server.port` (e.g. 8643) or it fails with
  `Could not bind 127.0.0.1:8642`.
- **kanban dispatcher**: only the primary gateway dispatches kanban jobs (the
  dispatcher lock lives at the root Hermes home).
- **DB rebuild** to change the bot profile or relay pin: stop the daemon unit,
  move the `simplex_db*` files aside, edit the unit, start. The gateway
  reconnects automatically (WS backoff).
