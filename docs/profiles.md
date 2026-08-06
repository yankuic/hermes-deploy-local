# Profiles & generic systemd templates

Hermes supports multiple named profiles, each with its own `HERMES_HOME`
(`~/.hermes/profiles/<name>`), config, state, and skills tree. The default
profile is the root install (`~/.hermes`). This document covers how a profile
is wired into systemd using the **generic `@` templates** and the launcher
scripts.

## Generic templates

The repository ships one unit file per service type, instantiated per profile:

| Template | Backing launcher | Purpose |
|----------|------------------|---------|
| `systemd/hermes-gateway@.service` | `scripts/hermes-gateway-run` | The Hermes agent gateway (LLM agent + API server + messaging platforms) |
| `systemd/simplex-daemon@.service` | `scripts/simplex-daemon-run` | The SimpleX chat daemon the gateway bot talks to |
| `systemd/qmd-mcp.service` | n/a | QMD MCP search (shared, not per-profile) |
| `systemd/graphify-rebuild-mcp.service` | `scripts/graphify-rebuild-mcp.py` | Graphify rebuild MCP (shared) |

`%i` in the templates is the profile name. The default install uses the
`@default` instance; every named profile is another instance — no new unit
file needed.

The launchers live in this repo and are referenced from the templates as
`%h/Hermes/scripts/…`, so clone this repo to `~/Hermes` (or edit the
`ExecStart=` paths). Both use `$HOME` at runtime, so no absolute user paths
appear in the repo.

### How the launchers map a profile

`scripts/hermes-gateway-run`:

- `default` → `HERMES_HOME=$HOME/.hermes`, bare `gateway run`.
- `<name>` → `HERMES_HOME=$HOME/.hermes/profiles/<name>`, `--profile <name> gateway run`.

`scripts/simplex-daemon-run`:

- Paths derive from the profile: `default` uses `$HOME/.hermes/cache/documents`;
  named profiles use `$HOME/.hermes/profiles/<name>/cache/documents`, an
  identity DB at `$HOME/.hermes/profiles/<name>/simplex_db`, and a
  `.simplex-tmp-<name>` temp folder.
- Ports and bot display names are **not** derivable — they are assigned in a
  small `case` table (default 5225, researcher 5226, jseeker 5227). For a new
  profile, add a case there, or set `SIMPLEX_PORT` / `SIMPLEX_DISPLAY_NAME`
  (e.g. in a systemd drop-in) instead.

## Creating a new profile (gateway + simplex daemon)

Example: create profile `analyst`.

1. **Create the profile.**

   ```bash
   hermes profile create analyst          # or: --clone to copy config/skills from the active profile
   ```

   This scaffolds `~/.hermes/profiles/analyst/` and a wrapper script.

2. **Configure the profile.** Edit `~/.hermes/profiles/analyst/config.yaml`:

   ```yaml
   agent:
     profile_scope: strict   # optional: strict file-tool isolation
   platforms:
     simplex:
       enabled: true
       extra:
         ws_url: ws://127.0.0.1:5230   # the daemon port for this profile
     api_server:
       port: 8650                      # unique per profile (default binds 8642)
   ```

   Copy `config/hermes.env.example` to `~/.hermes/profiles/analyst/.env` and set
   `SIMPLEX_WS_URL=ws://127.0.0.1:5230` (plus the other `SIMPLEX_*` vars).

3. **Give the new profile a SimpleX port + display name.** Add a case to
   `scripts/simplex-daemon-run`:

   ```bash
   analyst) port=5230; display="Hermes Analyst" ;;
   ```

   (or skip the case and pass `SIMPLEX_PORT` / `SIMPLEX_DISPLAY_NAME` via a
   `systemctl --user edit 'simplex-daemon@analyst.service'` drop-in).

4. **Copy the templates and enable the instances.**

   ```bash
   mkdir -p ~/.config/systemd/user
   cp systemd/hermes-gateway@.service systemd/simplex-daemon@.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now 'simplex-daemon@analyst.service'
   systemctl --user enable --now 'hermes-gateway@analyst.service'
   ```

5. **Verify.**

   ```bash
   systemctl --user is-active hermes-gateway@analyst simplex-daemon@analyst
   journalctl --user -u 'hermes-gateway@analyst.service' -f
   ```

## Creating a strict-isolated profile (step-by-step)

To run a named profile with **strict file-tool isolation** — its file tools
denied everything under the Hermes root outside its own tree — combine the
profile creation above with the `agent.profile_scope: strict` patch. The
mechanics and behavior matrix live in [`profile-isolation.md`](profile-isolation.md);
this section wires them together.

1. **Apply the isolation patch (one-time prerequisite).** The strict scope is
   enforced by a local patch on the hermes-agent install — without it,
   `profile_scope: strict` is a no-op:

   ```bash
   patches/apply-patches.sh                    # applies apply-profile-isolation.py (+ other local patches)
   # re-apply after every `hermes update`: patches/apply-profile-isolation.py --force
   ```

2. **Create the profile** (`hermes profile create analyst`), then edit
   `~/.hermes/profiles/analyst/config.yaml`:

   ```yaml
   agent:
     profile_scope: strict     # hard-denies reads/writes outside this profile's tree
     profile_scope_allow: []   # optional: re-share deliberate absolute Hermes-root paths,
                               # e.g. - /srv/shared/vault or the shared cache
   ```

3. **Expect profile-local state.** With strict scope, the shared
   `~/.hermes/cache`, `~/.hermes/config.yaml`, sibling profiles, and the
   default profile's `memories/`/`sessions/`/`skills/` are denied. Inbound
   SimpleX files must land in the profile-local cache —
   `scripts/simplex-daemon-run` already points named profiles at
   `~/.hermes/profiles/<name>/cache/documents/` (with a `<name>/simplex_db`
   identity DB), so the daemon stays isolated end-to-end.

4. **Isolate honcho memory** (otherwise the new profile shares memory with the
   default profile): add a profile-local `honcho.json` with a distinct
   workspace — template and gotchas in the *Per-profile isolation* section of
   [`docs/honcho.md`](honcho.md).

5. **Enable the instances** as in the walkthrough above:

   ```bash
   systemctl --user enable --now 'simplex-daemon@analyst.service'
   systemctl --user enable --now 'hermes-gateway@analyst.service'
   ```

6. **Verify the boundary.** From the profile's shell, reads of default-profile
   state are denied while its own tree works:

   ```bash
   hermes --profile analyst shell
   # read_file ~/.hermes/config.yaml       -> denied (belongs to default profile)
   # read_file ~/.hermes/profiles/analyst/config.yaml -> allowed
   ```

## Notes

- **Per-profile isolation**: a named profile's gateway runs with
  `HERMES_HOME=<root>/profiles/<name>`; with `agent.profile_scope: strict` its
  file tools are denied everything under the Hermes root outside its own tree.
  It has its own `simplex_db`, cache, memories, and skills. The gateway venv is
  the shared root one (`~/.hermes/hermes-agent/venv`).
- **Honcho memory isolation**: a new profile shares honcho memory with the
  default profile unless it gets its own profile-local `honcho.json` with a
  distinct workspace — see the *Per-profile isolation* section in
  `docs/honcho.md` for the config template and gotchas (the file goes at
  `~/.hermes/profiles/<name>/honcho.json`, keyed by host `hermes_<name>`).
- **Ports**: keep the daemon port, the profile's `api_server.port`, and the
  profile `SIMPLEX_WS_URL` consistent. Avoid the default gateway's
  `api_server` port (8642) and daemon port (5225).
- **kanban dispatcher**: only the primary (default) gateway dispatches kanban
  jobs — the dispatcher lock lives at the root Hermes home.
- **Existing profiles** built this way: `researcher` (daemon 5226, api 8643)
  and `jseeker` (daemon 5227, api 8644) — see `docs/simplex.md` and
  `docs/profile-isolation.md`.
