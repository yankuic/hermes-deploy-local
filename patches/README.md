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
| `apply_profile_isolation.py` | `agent/file_safety.py` (optional) | Per-profile filesystem isolation (hard-denies reads/writes of other profiles' data when running under a profile). Optional; see `docs/profile-isolation.md`. |

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
