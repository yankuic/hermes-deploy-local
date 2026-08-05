# Per-Profile Filesystem Isolation (local preview patch)

Hermes profiles are isolated in the sandbox (container mounts are profile-scoped),
but the agent's **file tools run host-side in the gateway process** — so without
this patch, a profile's agent can read/write the default profile's `config.yaml`,
`memories/`, `sessions/`, `SOUL.md`, and every other profile's state.

Upstream tracks this as a feature request (profile-scope config
`none | soft | strict`); this repo ships a working **strict-mode preview** as a
local patch mirroring upstream [PR #77605](
https://github.com/NousResearch/hermes-agent/pull/77605).

## What the patch does

`patches/apply-profile-isolation.py` (deployed copy:
`~/.hermes/patches/apply-profile-isolation.py`) modifies two files in the
hermes-agent install:

- `agent/file_safety.py` — strict-scope classification + read/write guards.
- `tools/file_tools.py` — moves the `read_file_tool` guard **before**
  structured-document (`.docx`/`.pdf`/`.xlsx`) extraction, so extracted text
  can't bypass the scope check.

It is **opt-in**: enforcement only activates when the active profile's
`config.yaml` sets `agent.profile_scope: strict`. The default profile
(`HERMES_HOME == root`) is unchanged.

## Usage

```bash
patches/apply-profile-isolation.py          # apply
patches/apply-profile-isolation.py --status # inspect
patches/apply-profile-isolation.py --revert # restore pre-patch file
patches/apply-profile-isolation.py --force  # re-apply after `hermes update`
```

Idempotent via a marker comment; hash-guarded so an updated (rewritten) file
refuses patching unless `--force` is given. Backups are written next to the
install as `<file>.pre-profile-isolation`, plus a `profile-isolation.json`
manifest. **Re-apply after every `hermes update`** via `patches/apply-patches.sh`.

## Behavior matrix (when `agent.profile_scope: strict` is set)

| Target | read | write |
|---|---|---|
| active profile's own tree (`<root>/profiles/<name>/**`) | allowed | allowed |
| `<root>/config.yaml` (default profile) | denied | denied |
| `<root>/memories/`, `sessions/`, `skills/`, `cron/`, `plugins/`, `sandboxes/` … | denied | denied |
| sibling `profiles/<other>/` | denied | denied |
| shared `<root>/cache/` | denied | denied |
| paths listed in `agent.profile_scope_allow` (absolute Hermes-root paths) | allowed | allowed |
| vault / anything outside the Hermes root | allowed | allowed |

`agent.profile_scope: none` (the default) leaves all behavior unchanged, and
`cross_profile=True` overrides cannot bypass the strict boundary.

The researcher profile is the active user of this: it sets
`agent.profile_scope: strict` (2026-08-04) and re-shares nothing via
`profile_scope_allow`, so its file tools are confined to its own tree — even
the shared `~/.hermes/cache` is denied, which is why the researcher's SimpleX
daemon writes inbound files to the profile-local
`~/.hermes/profiles/researcher/cache/documents/`.

## Verification

On a profile:

```bash
hermes --profile <name> shell
# read of a default-profile file should now be denied, e.g. read_file ~/.hermes/config.yaml
```

## Upstream references

- [NousResearch/hermes-agent#10376](https://github.com/NousResearch/hermes-agent/issues/10376)
  — "Profile isolation is incomplete: --clone copies memory, and agents can
  read across profile boundaries"
- PR #77605 (open; fixes #10376) — comments posted 2026-08-03 on #10376,
  #65693, #30585; see `memory-bank/feature-request-profile-isolation.md`
- #65693 (session reads), #30585 (umbrella), #25696 (existing soft guards)

The proposed upstream shape is a per-profile config knob:

```yaml
agent:
  profile_scope: strict        # none (default) | soft (warnings) | strict
  profile_scope_allow:         # extra roots outside the profile's own tree
    - /srv/shared/vault
```
