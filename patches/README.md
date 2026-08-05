# Patches

Local patches to the hermes-agent install tree. All upstream updates
(`hermes update`) overwrite the install, so **re-apply after every update**:

```bash
./apply-patches.sh          # apply everything (idempotent, skips applied)
./apply-patches.sh --status # report applied/not-applied per patch
```

Each script is anchor-based: it verifies the expected pristine code exists
before modifying, and refuses to double-apply. `--force` re-applies after a
manual revert/reinstall.

| Script | File patched | Fix |
|--------|-------------|-----|
| `image_source_output_mounts.py` | `tools/image_source.py` | Maps configured docker volumes (`<hermes_home>/cache/documents:/output` from `terminal.docker_volumes` / `gateway.docker_mount`) back to host paths so `vision_analyze` can read inbound SimpleX images without an active sandbox session. Without it: "not reachable inside the sandbox and no active sandbox session is available to read it". |
| `simplex_dm_send.py` | `plugins/platforms/simplex/adapter.py` | Outbound DMs use the structured `/_send @<id> json [...]` form. `simplex-chat` treats bare `@<id> text` as a display-name lookup and silently drops it (`contactNotFound`). |
| `simplex_inline_image.py` | `plugins/platforms/simplex/adapter.py` | SimpleX mobile clients deliver photos as inline base64 (`msgContent.image`), which the stock adapter drops. This surfaces them into `media_urls` so the vision pipeline analyzes them. |
| `apply-profile-isolation.py` | `agent/file_safety.py` + `tools/file_tools.py` | Local preview mirroring upstream PR #77605: opt-in `agent.profile_scope: strict` host file-tool isolation for named profiles (denies default/sibling state **and** the shared `~/.hermes/cache`; `agent.profile_scope_allow` re-shares deliberate paths; the `read_file_tool` guard is moved before `.docx/.pdf/.xlsx` extraction so structured docs can't bypass it). Only active when the profile's config sets `agent.profile_scope: strict`; the default profile is unchanged. Also writes `file_safety.py.pre-profile-isolation` / `file_tools.py.pre-profile-isolation` backups + a `profile-isolation.json` manifest next to the install. See `docs/profile-isolation.md` and `docs/sandbox-mounts.md`. |

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
