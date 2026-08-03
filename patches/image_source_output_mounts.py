#!/usr/bin/env python3
"""Apply the "output mount translation" patch to tools/image_source.py.

Maps configured docker volume mounts (e.g. ``<hermes_home>/cache/documents:/output``
from ``terminal.docker_volumes`` / ``gateway.docker_mount``) back to host paths
so ``vision_analyze`` can read inbound SimpleX images without an active sandbox
session. Without it, vision fails with "not reachable inside the sandbox and no
active sandbox session is available to read it".

Idempotent via a marker function; re-apply with ``--force`` after every
``hermes update`` (the updater rewrites the install tree).

Usage::

    python3 patches/image_source_output_mounts.py [--status] [--force]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

AGENT_DIR = Path(os.path.expanduser("~/.hermes/hermes-agent"))
TARGET = AGENT_DIR / "tools" / "image_source.py"
MARKER = "def _map_configured_output_mount"

HELPERS = '''_DOCKER_VOLUME_SPEC_RE = re.compile(r"^(?P<host>.+):(?P<container>/[^:]+?)(?::(?P<options>[^:]+))?$")
_CONFIGURED_MOUNT_MAP_CACHE = None


def _configured_docker_mount_map() -> dict:
    """Return ``{container_path: host_path}`` for configured docker volumes.

    Sources: ``TERMINAL_DOCKER_VOLUMES`` env (JSON list, when the runtime
    bridge already ran) or config.yaml ``terminal.docker_volumes`` +
    ``gateway.docker_mount``. Same spec shape gateway/run.py validates
    (``host:container[:options]``). Cached per process; a gateway restart
    picks up config changes.
    """
    global _CONFIGURED_MOUNT_MAP_CACHE
    if _CONFIGURED_MOUNT_MAP_CACHE is not None:
        return _CONFIGURED_MOUNT_MAP_CACHE

    mounts: dict = {}

    def _add(spec: str) -> None:
        match = _DOCKER_VOLUME_SPEC_RE.match(spec)
        if match:
            mounts[match.group("container").rstrip("/")] = match.group("host")

    try:
        raw = os.getenv("TERMINAL_DOCKER_VOLUMES", "")
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for spec in parsed:
                    _add(str(spec))
        else:
            import yaml

            from hermes_constants import get_hermes_home

            cfg_path = get_hermes_home() / "config.yaml"
            if cfg_path.is_file():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = yaml.safe_load(_f) or {}
                for spec in (cfg.get("terminal", {}).get("docker_volumes") or []):
                    _add(str(spec))
                gmount = (cfg.get("gateway") or {}).get("docker_mount")
                if gmount:
                    _add(str(gmount))
    except Exception:  # noqa: BLE001 - best-effort: fail open to existing routing
        pass
    _CONFIGURED_MOUNT_MAP_CACHE = mounts
    return mounts


def _map_configured_output_mount(p: Path, host_candidate: Path) -> Path:
    """Translate a container-side configured volume path to its host mount.

    E.g. ``/output/IMG_x.jpg`` -> ``~/.hermes/cache/documents/IMG_x.jpg``.
    Paths not under any configured mount are returned unchanged, so the
    caller's media-cache allowlist still gates every host read.
    """
    for container_path, host_path in _configured_docker_mount_map().items():
        try:
            rel = p.relative_to(Path(container_path))
        except ValueError:
            continue
        return Path(host_path) / rel
    return host_candidate
'''

CALL_BLOCK = '''    # Configured docker output volumes (e.g. gateway.docker_mount
    # '.../cache/documents:/output') are NOT part of the auto-mounted cache
    # list, so a model-supplied '/output/x.jpg' would otherwise fall through
    # to the in-sandbox read - which fails when no sandbox session is active
    # yet (vision_analyze is often the model's first tool call of a turn).
    # Translate them back to their host mount before the cache-root check.
    host_candidate = _map_configured_output_mount(p, host_candidate)
'''


def _read() -> str:
    return TARGET.read_text(encoding="utf-8")


def status() -> bool:
    return MARKER in _read()


def apply(force: bool = False) -> None:
    if status() and not force:
        print(f"already applied ({MARKER} present); use --force to re-apply")
        return
    src = _read()
    if status() and force:
        src = _read()  # re-apply: operations below are insertion-only, so
        # running on an already-patched file would duplicate. Refuse force
        # unless the marker is absent after all.
        raise SystemExit(
            "refusing to re-apply: patch is already present. Revert it manually "
            "or reinstall hermes-agent first."
        )

    if "import json" not in src:
        needle = "import base64\n"
        if needle not in src:
            raise SystemExit("anchor not found: 'import base64'")
        src = src.replace(needle, needle + "import json\n", 1)

    needle = "def _permitted_host_read_target("
    if needle not in src:
        raise SystemExit("anchor not found: 'def _permitted_host_read_target('")
    src = src.replace(needle, HELPERS + "\n\n" + needle, 1)

    needle = "    host_candidate = Path(from_agent_visible_cache_path(str(p)))\n"
    if needle not in src:
        raise SystemExit(
            "anchor not found: 'host_candidate = Path(from_agent_visible_cache_path(str(p)))'"
        )
    src = src.replace(needle, needle + CALL_BLOCK, 1)

    TARGET.write_text(src, encoding="utf-8")
    print(f"applied {TARGET}")


def main() -> None:
    parser = argparse.ArgumentParser(description="apply image_source output-mount patch")
    parser.add_argument("--status", action="store_true", help="check whether the patch is applied")
    parser.add_argument("--force", action="store_true", help="re-apply (only valid after revert/reinstall)")
    args = parser.parse_args()
    if args.status:
        print("applied" if status() else "not applied")
        return
    if not TARGET.is_file():
        raise SystemExit(f"target not found: {TARGET} (is hermes-agent installed?)")
    apply(force=args.force)


if __name__ == "__main__":
    main()
