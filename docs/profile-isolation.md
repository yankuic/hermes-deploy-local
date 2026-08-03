# Per-Profile Filesystem Isolation (local preview patch)

Hermes profiles are isolated in the sandbox (container mounts are profile-scoped),
but the agent's **file tools run host-side in the gateway process** — so without
this patch, a profile's agent can read/write the default profile's `config.yaml`,
`memories/`, `sessions/`, `SOUL.md`, and every other profile's state.

Upstream tracks this as a feature request (profile-scope config
`none | soft | strict`); this repo ships a working **strict-mode preview** as a
local patch.

## What the patch does

`patches/apply_profile_isolation.py` modifies `agent/file_safety.py`:

- **Only active when running under a profile** (`HERMES_HOME` under
  `<root>/profiles/<name>/`). The default profile is byte-identical.
- **Reads**: paths under `<root>/profiles/<other>/` or top-level default-profile
  state (`config.yaml`, `memories/`, `sessions/`, `cron/`, `skills/`,
  `plugins/`, `sandboxes/`, `kanban/`, `state.db`, …) are denied.
- **Writes**: the same paths are hard-denied in the write-deny classification,
  which runs before the `cross_profile=True` soft-guard — the override is ignored.
- **Still allowed**: the active profile's own tree, the shared `<root>/cache/`,
  and everything outside the Hermes root (e.g. an Obsidian vault).

## Usage

```bash
patches/apply_profile_isolation.py          # apply
patches/apply_profile_isolation.py --status # inspect
patches/apply_profile_isolation.py --revert # restore pre-patch file
patches/apply_profile_isolation.py --force  # re-apply after `hermes update`
```

Idempotent via a marker comment; hash-guarded so an updated (rewritten) file
refuses patching unless `--force` is given. **Re-apply after every
`hermes update`.**

## Behavior matrix (when applied)

| Target | read | write |
|---|---|---|
| `<root>/config.yaml` (default profile) | denied | denied |
| `<root>/memories/`, `sessions/`, `skills/` (default) | denied | denied |
| sibling `profiles/<other>/` | denied | denied |
| own profile tree (`profiles/<name>/**`) | allowed | allowed |
| shared `<root>/cache/` | allowed | allowed |
| vault (outside Hermes root) | allowed | allowed |

## Verification

On a profile:

```bash
hermes -p <name> shell
# read of a default-profile file should now be denied, e.g. read_file ~/.hermes/config.yaml
```

Default profile behavior is unchanged: `hermes shell` file tools work as before.

## Upstream references

- [NousResearch/hermes-agent#10376](https://github.com/NousResearch/hermes-agent/issues/10376)
  — "Profile isolation is incomplete: --clone copies memory, and agents can
  read across profile boundaries"
- #65693 (session reads), #30585 (umbrella), #25696 (existing soft guards)

The proposed upstream shape is a per-profile config knob:

```yaml
agent:
  profile_scope: strict        # none (default) | soft (warnings) | strict
  profile_scope_allow:         # extra roots outside the profile's own tree
    - /srv/shared/vault
```
