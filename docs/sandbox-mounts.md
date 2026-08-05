# Mounting paths with a sandboxed Hermes deployment

How to share host directories with the Hermes Docker sandbox and make the
paths usable from the gateway (media delivery, vision, file tools).

## Two path worlds

Hermes with `terminal.backend: docker` runs the agent's terminal inside a
container. There are two different views of the filesystem:

- **Container paths** — what the model sees in the sandbox (e.g. `/workspace`,
  `/root`, `/output`).
- **Host paths** — what the gateway process and the file tools operate on.

Anything consumed *outside* the sandbox — chat media delivery (`MEDIA:` tags),
vision reads, host-side file tools — resolves **host** paths only. There is no
container→host translation for arbitrary mounts in the media-delivery path, so
a path must be valid on the host before it can render in chat.

## Mount configuration surfaces

### `terminal.docker_volumes`

The primary mount config. A list of entries in standard Docker `-v` syntax:
`host_path:container_path[:options]`.

```yaml
terminal:
  backend: docker
  docker_volumes:
    - "/home/user/projects:/workspace/projects"   # read-write (default)
    - "/home/user/datasets:/data:ro"              # read-only
    - "/home/user/.hermes/cache/documents:/output" # gateway-visible exports
```

Also settable via env: `TERMINAL_DOCKER_VOLUMES='["/host:/container"]'`
(JSON array).

> **YAML gotcha:** duplicate keys silently override earlier ones. Merge new
> mounts into the *same* `docker_volumes:` list; do not add a second
> `docker_volumes:` key later in the file.

### `gateway.docker_mount`

A single `host:container` export mount that feeds the mount map used by vision
reads and media-path handling. Mirrors one of your `docker_volumes` entries.

```yaml
gateway:
  docker_mount: /home/user/.hermes/cache/documents:/output
```

### Auto-generated mounts (not configurable)

Hermes adds these automatically:

| Container path | Host source | Mode |
|---|---|---|
| `/root` | sandbox `home` dir under the Hermes root | rw |
| `/workspace` | sandbox `workspace` dir under the Hermes root | rw |
| `/root/.hermes/skills` | profile `skills/` | ro |
| `/root/.hermes/cache/{documents,images,audio,videos,screenshots}` | profile `cache/<subdir>` | ro |

## Mounting patterns by purpose

### Read-only reference data

Give the agent datasets, configs, or reference code without letting it mutate
the source:

```yaml
- "/home/user/datasets:/data:ro"
```

### Shared read-write workspace

Default (rw) is fine when both you and the agent should be able to write:

```yaml
- "/home/user/projects:/workspace/projects"
```

### Chat-renderable deliverable directory (the important one)

To render files the model generates (images, PDFs, reports) in chat, the model
emits a `MEDIA:<path>` tag, and the gateway delivers it **only if that exact
path resolves on the host**. The model only knows container paths, so the
reliable pattern is a **1:1 mount**: use the *same* path string on both sides.

```yaml
terminal:
  docker_volumes:
    - "/home/user/.hermes/profiles/researcher/output:/home/user/.hermes/profiles/researcher/output"
```

Now the sandbox path is identical to the host path, so
`MEDIA:/home/user/.hermes/profiles/researcher/output/plot.png` renders with no
translation and no special model instructions. This is why a vault mounted
1:1 "just works".

### Legacy `/output` convention

`host:/output` (or `/outputs`) is the recognized export-mount container path,
and the documented example is `<HERMES_HOME>/cache/documents:/output`. It is a
*convenience alias only*: `/output/...` is still not a host path, so the model
must emit the **host** path in `MEDIA:` (e.g.
`MEDIA:/home/user/.hermes/cache/documents/report.txt`) for it to render. Do
not emit `/workspace/...` or `/output/...` unless that exact path also exists
for the gateway on the host.

### Canonical cache directories

`<HERMES_HOME>/profiles/<profile>/cache/{documents,images,audio,videos,screenshots}`
are delivery-allowlisted (accepted in both strict and non-strict mode). They
are mounted **read-only** in the sandbox, so files there are written by the
host-side file tool or the gateway, not from the sandbox terminal.

## Security rules

- **Never mount the profile directory or the Hermes root read-write** into the
  sandbox. Doing so exposes `.env`, `config.yaml`, `auth.json`, databases,
  `memories/`, and `sessions/` to a networked, possibly cloud-hosted model.
  Mount only the subdirectories the agent actually needs.
- **Use `:ro`** for anything the model only needs to read.
- **Host file-tool scope**: named profiles can set
  `agent.profile_scope: strict` (see `docs/profile-isolation.md`)
  to deny file-tool reads/writes to anything under the Hermes root outside
  their own tree — including the shared `~/.hermes/cache`. Deliberately shared
  paths (e.g. a cache) can be re-allowed with `agent.profile_scope_allow`.
- **Delivery denylist** — the gateway will not deliver (and you should not
  mount) these: `/etc`, `/proc`, `/sys`, `/dev`, `/root`, `/boot`,
  `/var/log`, `/var/lib`, `/var/run`, `~/.ssh`, `~/.aws`, `~/.gnupg`,
  `~/.kube`, `~/.docker`, `~/.config`, `~/.azure`, `~/.gcloud`, Hermes-root
  credential files (`.env`, `auth.json`, `config.yaml`, `credentials`, …),
  `pairing/`, `mcp-tokens/`.

## Delivery rules for sandbox-generated files

- The `MEDIA:` path must resolve to an existing file **on the host** and must
  not be under the denylist. This is the default (non-strict) behavior.
- If `gateway.strict` is enabled, the directory must also be listed in
  `gateway.media_delivery_allow_dirs` (or the file freshly produced within the
  recency window).
- Known gap: an alias mount (container path ≠ host path, e.g.
  `/home/researcher/output`) does **not** render from the container path —
  there is no delivery-side container→host translation. A patch is proposed
  but not applied. See `memory-bank/media-delivery-output-path.md`.

## Worked example

```yaml
terminal:
  backend: docker
  docker_volumes:
    # 1:1 vault mount — renders in chat natively (container path == host path)
    - "/home/user/Obsidian/vault:/home/user/Obsidian/vault"
    # 1:1 deliverable dir — sandbox-generated files render in chat
    - "/home/user/.hermes/profiles/researcher/output:/home/user/.hermes/profiles/researcher/output"
    # read-only reference data
    - "/home/user/datasets:/data:ro"
gateway:
  docker_mount: /home/user/.hermes/profiles/researcher/output:/home/user/.hermes/profiles/researcher/output
```

Notes on the example:
- The vault and the deliverable dir are mounted 1:1, so the model's natural
  paths are host-resolvable and render.
- `/workspace` and `/root` are sandbox-owned (auto-mounted); do not rely on
  them for deliverables.
- Cache dirs are auto-mounted `ro`; write those via the host-side file tool.
