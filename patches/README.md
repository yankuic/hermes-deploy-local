# Patches

Local patches to the hermes-agent install tree. All upstream updates
(`hermes update`) overwrite the install, so **re-apply after every update**:

```bash
./apply-patches.sh          # apply everything (idempotent, skips applied)
./apply-patches.sh --status # report applied/not-applied per patch
```

Each script is anchor-based: it verifies the expected pristine code exists
before modifying, and refuses to double-apply. `--force` re-applies after a
manual revert/reinstall. Patches that back an open upstream PR retire
automatically (per-hunk) once the PR merges and `hermes update` pulls it —
`apply-patches.sh --status` reports what's still needed.

| Script | File patched | Fix | Fixed in |
|--------|-------------|-----|----------|
| `image_source_output_mounts.py` | `tools/image_source.py` | Maps configured docker volumes (`<hermes_home>/cache/documents:/output` from `terminal.docker_volumes` / `gateway.docker_mount`) back to host paths so `vision_analyze` can read inbound SimpleX images without an active sandbox session. Without it: "not reachable inside the sandbox and no active sandbox session is available to read it". | N/A — local workaround; no upstream equivalent identified |
| ~~`simplex_dm_send.py`~~ retired 2026-08-09 | `plugins/platforms/simplex/adapter.py` | Outbound DMs use the structured `/_send @<id> json [...]` form. Now native upstream (`send()` + `_standalone_send()`), so the patch has no bare-form target left. File kept for reference; not run by `apply-patches.sh`. | **Fixed natively in upstream `main` (2026-08-09)** — already retired |
| `simplex_inline_image.py` | `plugins/platforms/simplex/adapter.py` | SimpleX mobile clients deliver photos as inline base64 (`msgContent.image`), which the stock adapter drops. This surfaces them into `media_urls` so the vision pipeline analyzes them. | N/A — local workaround; upstream fix undetermined |
| `apply-profile-isolation.py` | `agent/file_safety.py` + `tools/file_tools.py` | Local preview mirroring upstream PR #77605: opt-in `agent.profile_scope: strict` host file-tool isolation for named profiles (denies default/sibling state **and** the shared `~/.hermes/cache`; `agent.profile_scope_allow` re-shares deliberate paths; the `read_file_tool` guard is moved before `.docx/.pdf/.xlsx` extraction so structured docs can't bypass it). Only active when the profile's config sets `agent.profile_scope: strict`; the default profile is unchanged. Also writes `file_safety.py.pre-profile-isolation` / `file_tools.py.pre-profile-isolation` backups + a `profile-isolation.json` manifest next to the install. See `docs/profile-isolation.md` and `docs/sandbox-mounts.md`. | Pending upstream **PR #77605** (open, unmerged as of 2026-08-09) |
| `desktop_slash_live_gateway.py` | `apps/desktop/src/app/chat/composer/hooks/use-slash-completions.ts` + `use-live-completion-adapter.ts` (+ test file) | Backs upstream PR #80121 (open/unmerged): the slash completions hook resolves the **live active gateway** (`$gateway`) so `commands.catalog`/`complete.slash` stop hitting a stale socket after profile switching, plus a hardening layer that re-throws slash-fetch failures so a transient error (profile backend mid-spawn / socket mid-reconnect) self-heals on the next `/` open instead of leaving the menu stuck empty until app restart. Marker-based/idempotent; auto-retires per-hunk when upstream merges. Rebuild after applying: `hermes desktop --build-only`. | Pending upstream **PR #80121** (open, unmerged as of 2026-08-09); the failure-recovery hardening layer is local-only |
| `desktop_launcher_wrapper.py` | `hermes_cli/linux_desktop_entry.py` | Makes `resolve_exec_command()` prefer `~/.local/bin/hermes-desktop` (when present/executable) for the `Exec=` line that `hermes desktop`/`hermes update` rewrite into `~/.local/share/applications/hermes.desktop`. Without it, the entry points at the venv `hermes` binary, which silently fails from GNOME (minimal PATH lacks the nvm node dir needed for `hermes desktop`'s npm check). See `memory-bank/hermes-config.md` "Desktop App Launcher". | N/A — local environment workaround (GNOME minimal PATH), not an upstream bug |

## Sanity checks (after applying)

```bash
# image_source: resolve a test image through the /output mount
cd ~/.hermes/hermes-agent && TERMINAL_ENV=docker ./venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from tools import image_source
import asyncio
async def main():
    r = await image_source.resolve_image_source('/output/test.jpg', image_source.ResolveContext())
    print(r.origin, r.mime, len(r.data))
asyncio.run(main())"

# adapter: send() / _standalone_send() use the structured form
grep -n '/_send' ~/.hermes/hermes-agent/plugins/platforms/simplex/adapter.py
```

Deployed copy note: the profile-isolation script lives at
`~/.hermes/patches/apply-profile-isolation.py` (with its `.pre-profile-isolation`
backups and `profile-isolation.json` manifest). The project copy in this
directory is kept in sync with it — redeploy with
`cp patches/apply-profile-isolation.py ~/.hermes/patches/` if the runtime copy
drifts. See `memory-bank/feature-request-profile-isolation.md` for the upstream
ticket tracking.
