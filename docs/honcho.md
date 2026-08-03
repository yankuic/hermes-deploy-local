# Honcho Memory Layer Setup

How [Honcho](https://github.com/plastic-labs/honcho) is deployed as the memory
layer for Hermes, using **local models only** (Ollama on the LAN — no cloud LLM
dependency for memory).

Honcho ingests messages in the background, extracts durable facts
("conclusions") about the user, and exposes them to Hermes through five memory
tools (`honcho_profile`, `honcho_search`, `honcho_reasoning`,
`honcho_context`, `honcho_conclude`) plus automatic dialectic context injection.

## Architecture

```
Hermes Gateway ──→ http://127.0.0.1:8100 ──→ Honcho API (docker)
                                                   │
                                  queue items (Postgres)
                                                   ▼
                                  Honcho Deriver (docker worker)
                                                   │
                       ┌───────────────────────────┤
                       ▼                           ▼
              PostgreSQL (pgvector)          Ollama <OLLAMA_HOST>:11434
              Redis                          qwen3:8b (text), qwen3-embedding:0.6b
```

- `memory.provider: honcho` in Hermes config + `honcho.json` in `~/.hermes`
- Honcho runs via docker compose (API on `127.0.0.1:8100`, Postgres, Redis)

## Prerequisites

- Docker Engine + Compose v2
- Ollama host with `qwen3:8b` (text: deriver/dialectic/summary/dream) and
  `qwen3-embedding:0.6b` (embeddings, 1024 dims)
- Free host ports: `8100` (API), `5433` (Postgres), `6379` (Redis) — adjust if
  other services occupy them

## 1. Deploy Honcho (docker compose)

```bash
git clone <honcho-repo>
cd honcho
cp docker-compose.yml.example docker-compose.yml
cp .env.template .env        # edit per step 3
docker compose up -d --build
```

Ports differ from the example if 8000/5432 are occupied locally — map the api
to `127.0.0.1:8100:8000` and the database to `127.0.0.1:5433:5432`.

**Local repo modification (non-1536 embeddings only):** three Alembic
migrations hardcode `Vector(1536)`, which fails startup validation when
`EMBEDDING_VECTOR_DIMENSIONS` is not 1536. Patch them to read
`settings.EMBEDDING.VECTOR_DIMENSIONS`:

- `migrations/versions/a1b2c3d4e5f6_initial_schema.py`
- `migrations/versions/917195d9b5e9_add_messageembedding_table.py`
- `migrations/versions/119a52b73c60_support_external_embeddings.py`

If the DB volume already exists with the wrong dimension:
`docker compose down -v && docker compose up -d` (destructive).

## 2. Ollama host setup

Raise the context window so deriver/dialectic inputs (up to tens of thousands
of tokens) are not truncated at Ollama's 4096 default:

```ini
# systemctl edit ollama (on the Ollama host)
[Service]
Environment="OLLAMA_CONTEXT_LENGTH=32768"
```

Memory caveat: 32k context ≈ +2-3 GB (8B model) KV cache; use 16384 on tight hosts.

## 3. Configure Honcho (`.env`)

Every model config block needs its own `*_OVERRIDES__BASE_URL` — there is no
global override:

```env
LLM_OPENAI_API_KEY=ollama

# Process work units immediately (no 512-token accumulation gate)
DERIVER_REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS=0

DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=http://<OLLAMA_HOST>:11434/v1
DERIVER_MODEL_CONFIG__MODEL=qwen3:8b

SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL=http://<OLLAMA_HOST>:11434/v1
SUMMARY_MODEL_CONFIG__MODEL=qwen3:8b

DIALECTIC_LEVELS__minimal__MODEL_CONFIG__OVERRIDES__BASE_URL=http://<OLLAMA_HOST>:11434/v1
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__MODEL=qwen3:8b
# ... repeat for low / medium / high / max ...

DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://<OLLAMA_HOST>:11434/v1
DREAM_DEDUCTION_MODEL_CONFIG__MODEL=qwen3:8b
DREAM_INDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://<OLLAMA_HOST>:11434/v1
DREAM_INDUCTION_MODEL_CONFIG__MODEL=qwen3:8b

EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=http://<OLLAMA_HOST>:11434/v1
EMBEDDING_MODEL_CONFIG__MODEL=qwen3-embedding:0.6b
EMBEDDING_VECTOR_DIMENSIONS=1024

CORS_ORIGINS=["http://localhost","http://127.0.0.1:8100","https://api.honcho.dev"]
```

Key decisions:

- `qwen3:8b` for text: decent tool-calling/JSON support at acceptable local
  speed. A 27B model is several times slower per background call.
- `DERIVER_REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS=0`: memory extracted
  within ~1 poll cycle of each message instead of waiting for 512 tokens or the
  30-min age flush. Trade-off: more, smaller LLM calls under sustained traffic.
- Auth off (`AUTH_USE_AUTH=false`), API bound to localhost — acceptable for a
  personal host install.

## 4. Configure Hermes (`~/.hermes/honcho.json`)

Template: `config/honcho.json.example`. Notable fields:

- **`baseUrl` must include the scheme** (`http://127.0.0.1:8100`), or every
  session init fails with `Request URL is missing an 'http://' or 'https://' protocol.`
- `observationMode: "unified"` — user peer modeled, AI peer not (halves
  background deriver work)
- `reasoningLevelCap: "low"` — bounds dialectic reasoning level; on local
  inference `high` = up to 4 tool iterations = multi-minute answers
- `dialecticCadence: 2` — context refresh every 2 turns
- `recallMode: "hybrid"` — injects context AND exposes the five tools
- **`"timeout": 180`** — the 30 s default HTTP timeout is too short for local
  dialectic calls (see Troubleshooting)

Apply with a gateway restart: `systemctl --user restart hermes-gateway.service`.

## Per-profile isolation

Each named profile can get its own honcho workspace via a profile-local file.
Config resolution priority:

1. `$HERMES_HOME/honcho.json` (profile-local — wins)
2. `~/.hermes/honcho.json` (default profile)
3. `~/.honcho/config.json` (global)

The host key derives from the active profile (`hermes` default,
`hermes_<profile>` otherwise). Without a profile-local file, a named profile
falls back to the shared file — and if that block sets `"workspace": "hermes"`,
the profile **shares memory with the default profile** (honcho memory is keyed
by workspace + peer).

To isolate, create `~/.hermes/profiles/<name>/honcho.json` with a distinct
workspace:

```json
{
  "hosts": {
    "hermes_<name>": {
      "peerName": "<USER_NAME>",
      "aiPeer": "<name>",
      "workspace": "hermes_<name>",
      "recallMode": "hybrid",
      "writeFrequency": "async",
      "sessionStrategy": "per-session",
      "enabled": true,
      "saveMessages": true
    }
  },
  "baseUrl": "http://127.0.0.1:8100"
}
```

Gotchas:

- Honcho tool exposure is gated on the `memory` toolset — if the 5 honcho tools
  never appear, check the profile's `agent.disabled_toolsets` for `memory`.
- The new workspace starts empty; old data is not migrated.
- A stale `hermes_<profile>` block in the shared file becomes dead config once
  the profile-local file exists — safe to leave or delete.

## Verification

```bash
curl http://localhost:8100/health                     # {"status":"ok"}
docker compose ps                                     # all healthy

# after a conversation, conclusions should appear within ~1-3 min:
curl -s -X POST http://localhost:8100/v3/workspaces/<ws>/conclusions/list \
  -H "Content-Type: application/json" -d '{"peer_ids": ["<peer>"]}'
```

Smoke test: send "I love trail running and my dog is named Biscuit"; expect
conclusions `Loves trail running.` and `Has a dog named Biscuit.` within ~2 min.

## Troubleshooting

- **`Request URL is missing an 'http://'...`** — `baseUrl` lacks the scheme.
- **`documents.embedding dim (1536) does not match EMBEDDING_VECTOR_DIMENSIONS (1024)`** —
  DB volume created with a different dimension: `docker compose down -v && up -d`
  (destructive).
- **Deriver never claims work units** — the 512-token gate holds batches up to
  30 min; set `DERIVER_REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS=0`.
- **`Honcho dialectic query failed: Request timed out after 30.0s`** — set
  `"timeout": 180` in the host block (`~/.hermes/honcho.json`), restart gateway.
- **`the input length exceeds the context length` (400) flooding deriver logs /
  embeddings stuck `failed`** — embedding model context too small (e.g.
  `mxbai-embed-large` has a hard 512-token cap while honcho's
  `EMBEDDING_MAX_INPUT_TOKENS=8192` sends oversized inputs, failing the whole
  batch). Use `qwen3-embedding:0.6b` (32k context, 1024 dims). To re-embed after
  a model switch: `DELETE FROM message_embeddings;` +
  `UPDATE documents SET embedding=NULL, sync_state='pending';` then
  `docker compose exec deriver python scripts/generate_message_embeddings.py`.
  Avoid `bge-m3` on older Ollama: it returns NaN for certain token sequences.
