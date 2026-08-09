#!/usr/bin/env python3
"""Apply the desktop slash-command profile-switch fix to apps/desktop.

Backs upstream PR #80121 (fix/desktop-slash-catalog-stale-gateway) plus a
local hardening layer. The Desktop slash completions hook captures the
``gateway`` prop at session mount; profile switches SWAP the active gateway
object (store/gateway.ts activeGateway()), so sessions that outlive a switch
kept sending ``commands.catalog`` / ``complete.slash`` to the old connection.
The request failed, the fetcher swallowed the error, and the completion
adapter recorded the empty result as the answer to ``/`` — the menu stayed
broken in every profile until the app was restarted.

This patch:
  1. Resolves the LIVE active gateway ($gateway) inside the fetcher (PR #80121).
  2. Re-throws slash-fetch failures instead of returning a settled empty
     payload, and makes the completion adapter leave the query "unknown" on
     failure — so a transient error (profile backend mid-spawn, socket
     mid-reconnect, gateway swap) self-heals on the next ``/`` open.

Idempotent via marker strings; auto-retires per-hunk once upstream main
carries the same code (e.g. PR #80121 merges). Re-apply after every
``hermes update`` with ``python3 patches/desktop_slash_live_gateway.py``.

Usage::

    python3 patches/desktop_slash_live_gateway.py [--status] [--force]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

AGENT_DIR = Path(os.environ.get("HERMES_AGENT_DIR") or os.path.expanduser("~/.hermes/hermes-agent"))
SLASH_HOOK = (
    AGENT_DIR
    / "apps"
    / "desktop"
    / "src"
    / "app"
    / "chat"
    / "composer"
    / "hooks"
    / "use-slash-completions.ts"
)
ADAPTER_HOOK = (
    AGENT_DIR
    / "apps"
    / "desktop"
    / "src"
    / "app"
    / "chat"
    / "composer"
    / "hooks"
    / "use-live-completion-adapter.ts"
)
SLASH_TEST = (
    AGENT_DIR
    / "apps"
    / "desktop"
    / "src"
    / "app"
    / "chat"
    / "composer"
    / "hooks"
    / "use-slash-completions.test.tsx"
)

# Per-hunk markers. Present = this hunk is already applied (skip).
MARKER_LIVE_GATEWAY = "const currentGateway = useStore($gateway)"
MARKER_GW_GUARD = "const gw = currentGateway ?? gateway"
MARKER_RETHROW = "throw new Error('slash completion request failed')"
MARKER_ADAPTER_RETRY = "pendingQueryRef.current = null\n            setState({ query: EMPTY_QUERY, items: [] })"
MARKER_TEST_GATEWAY_SWAP = "routes slash completions through the live active gateway after a profile swap"
MARKER_TEST_RETRY = "retries a failed catalog fetch on the next open instead of sticking empty"

# (marker, target-text, replacement-text) — replacement only applied when the
# marker is absent AND target-text is present.
HUNKS = [
    (
        MARKER_LIVE_GATEWAY,
        SLASH_HOOK,
        "import { normalize } from '@/lib/text'\n",
        "import { normalize } from '@/lib/text'\n"
        "import { $gateway } from '@/store/gateway'\n",
    ),
    (
        MARKER_LIVE_GATEWAY,
        SLASH_HOOK,
        "  const epoch = useStore($slashCompletionsEpoch)\n",
        "  const epoch = useStore($slashCompletionsEpoch)\n"
        "  // Live active gateway: profile switches SWAP the gateway object\n"
        "  // (store/gateway.ts activeGateway()), so a mount-time prop is stale for\n"
        "  // sessions that outlive a switch. Subscribing recreates the fetcher on swap.\n"
        "  const currentGateway = useStore($gateway)\n",
    ),
    (
        MARKER_GW_GUARD,
        SLASH_HOOK,
        "      if (!gateway) {\n        return { items: [], query }\n      }\n",
        "      // Prefer the LIVE active gateway; fall back to the mount-time prop.\n"
        "      const gw = currentGateway ?? gateway\n\n"
        "      if (!gw) {\n        return { items: [], query }\n      }\n",
    ),
    (
        "gw.request<CommandsCatalogLike>('commands.catalog')",
        SLASH_HOOK,
        "gateway.request<CommandsCatalogLike>('commands.catalog')",
        "gw.request<CommandsCatalogLike>('commands.catalog')",
    ),
    (
        "gw.request<{ items?: CompletionEntry[]; replace_from?: number }>('complete.slash', { text })",
        SLASH_HOOK,
        "gateway.request<{ items?: CompletionEntry[]; replace_from?: number }>('complete.slash', { text })",
        "gw.request<{ items?: CompletionEntry[]; replace_from?: number }>('complete.slash', { text })",
    ),
    (
        "currentGateway, skinThemes, activeSkin",
        SLASH_HOOK,
        "[gateway, skinThemes, activeSkin]",
        "[gateway, currentGateway, skinThemes, activeSkin]",
    ),
    (
        MARKER_RETHROW,
        SLASH_HOOK,
        "      } catch {\n        return { items: [], query }\n      }\n",
        "      } catch {\n"
        "        // Re-throw rather than returning a settled empty payload: the\n"
        "        // completion adapter must know the lookup FAILED. If we returned\n"
        "        // `{ items: [], query }` here, the adapter would record the empty\n"
        "        // result as the answer to `/` and never refetch until a profile switch\n"
        "        // or restart — the stuck-menu symptom after a transient failure\n"
        "        // (profile backend mid-spawn, socket mid-reconnect, gateway swap).\n"
        "        throw new Error('slash completion request failed')\n"
        "      }\n",
    ),
    (
        "pendingQueryRef.current = null\n            setState({\n              query: payload.query,",
        ADAPTER_HOOK,
        "            setState({\n              query: payload.query,",
        "            pendingQueryRef.current = null\n"
        "            setState({\n              query: payload.query,",
    ),
    (
        MARKER_ADAPTER_RETRY,
        ADAPTER_HOOK,
        "            setState({ query, items: [] })",
        "            // Do NOT record the failed query as a settled answer: leave the\n"
        "            // adapter in the \"unknown query\" state so the next search()\n"
        "            // retries instead of serving a stuck empty result until a profile\n"
        "            // switch or restart (transient failures: profile backend\n"
        "            // mid-spawn, socket mid-reconnect, gateway swap during a switch).\n"
        "            pendingQueryRef.current = null\n"
        "            setState({ query: EMPTY_QUERY, items: [] })",
    ),
    (
        MARKER_TEST_GATEWAY_SWAP,
        SLASH_TEST,
        "import { invalidateSlashCompletions } from '@/lib/slash-completion-cache'\n",
        "import { invalidateSlashCompletions } from '@/lib/slash-completion-cache'\n"
        "import { $gateway } from '@/store/gateway'\n",
    ),
    (
        MARKER_TEST_GATEWAY_SWAP,
        SLASH_TEST,
        "afterEach(() => {\n  cleanup()\n  queryClient.clear()\n})",
        "afterEach(() => {\n  cleanup()\n  $gateway.set(null)\n  queryClient.clear()\n})",
    ),
    (
        MARKER_TEST_GATEWAY_SWAP,
        SLASH_TEST,
        "describe('useSlashCompletions', () => {\n  it('serves the bare-slash catalog from cache instead of re-requesting it', async () => {",
        "describe('useSlashCompletions', () => {\n"
        "  it('routes slash completions through the live active gateway after a profile swap', async () => {\n"
        "    const gatewayA = { request: vi.fn().mockResolvedValue(CATALOG) } as unknown as HermesGateway\n"
        "    const gatewayB = { request: vi.fn().mockResolvedValue(CATALOG) } as unknown as HermesGateway\n"
        "    const api = harness(gatewayA)\n\n"
        "    // Warm the catalog through the mount-time gateway A.\n"
        "    await completions(api, '')\n"
        "    expect(gatewayA.request).toHaveBeenCalledWith('commands.catalog')\n"
        "    expect(gatewayB.request).not.toHaveBeenCalled()\n\n"
        "    // Profile switches swap the active gateway object ($gateway). The fetcher\n"
        "    // must now use the LIVE gateway, not the one captured at mount time.\n"
        "    await act(async () => $gateway.set(gatewayB))\n\n"
        "    await completions(api, 'res')\n"
        "    expect(gatewayB.request).toHaveBeenCalledWith('complete.slash', { text: '/res' })\n"
        "    expect(gatewayA.request).not.toHaveBeenCalledWith('complete.slash', { text: '/res' })\n"
        "  })\n\n"
        "  it('retries a failed catalog fetch on the next open instead of sticking empty', async () => {\n"
        "    // Transient failure (profile backend mid-spawn / socket mid-reconnect).\n"
        "    const request = vi\n"
        "      .fn()\n"
        "      .mockRejectedValueOnce(new Error('Hermes gateway connection closed'))\n"
        "      .mockResolvedValue(CATALOG)\n\n"
        "    const api = harness({ request } as unknown as HermesGateway)\n\n"
        "    // First open: the fetch fails, so the menu is empty — and the failed query\n"
        "    // must NOT be recorded as a settled answer.\n"
        "    await act(async () => {\n"
        "      api.search('')\n"
        "      await new Promise(resolve => setTimeout(resolve, 120))\n"
        "    })\n"
        "    expect(commandsOf(api.search(''))).toEqual([])\n"
        "    expect(request).toHaveBeenCalledTimes(1)\n\n"
        "    // Reopening `/` refetches (same mount) instead of serving the empty result.\n"
        "    await act(async () => {\n"
        "      api.search('')\n"
        "      await new Promise(resolve => setTimeout(resolve, 120))\n"
        "    })\n"
        "    expect(commandsOf(api.search(''))).toContain('/new')\n"
        "    expect(request).toHaveBeenCalledTimes(2)\n"
        "  })\n\n"
        "  it('serves the bare-slash catalog from cache instead of re-requesting it', async () => {",
    ),
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def status() -> list[tuple[str, bool]]:
    """Return [(label, applied)] for the production-source hunks (not tests)."""
    return [
        ("live-gateway", MARKER_LIVE_GATEWAY in _read(SLASH_HOOK)),
        ("retry-on-failure", MARKER_ADAPTER_RETRY in _read(ADAPTER_HOOK)),
    ]


def apply(force: bool = False) -> None:
    failures = 0
    skipped = 0
    applied = 0
    cache: dict[Path, str] = {}

    def src(path: Path) -> str:
        if path not in cache:
            cache[path] = _read(path)
        return cache[path]

    for marker, path, target, replacement in HUNKS:
        content = src(path)
        if marker in content:
            skipped += 1
            continue
        if target not in content:
            print(f"  [skip] {path.name}: target not found ({marker})")
            failures += 1
            continue
        cache[path] = content.replace(target, replacement, 1)
        applied += 1

    for path, content in cache.items():
        path.write_text(content, encoding="utf-8")

    print(f"desktop_slash_live_gateway: {applied} hunk(s) applied, {skipped} already present, {failures} target(s) missing")
    if failures:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="apply desktop slash-command live-gateway patch")
    parser.add_argument("--status", action="store_true", help="check whether the patch is applied")
    parser.add_argument("--force", action="store_true", help="re-apply (replaces any existing hunk text)")
    args = parser.parse_args()

    missing = [p for p in (SLASH_HOOK, ADAPTER_HOOK, SLASH_TEST) if not p.is_file()]
    if missing:
        raise SystemExit(f"target not found: {missing[0]} (is hermes-agent installed?)")

    if args.status:
        for label, applied in status():
            print(f"{label}: {'applied' if applied else 'not applied'}")
        return

    apply(force=args.force)


if __name__ == "__main__":
    main()
