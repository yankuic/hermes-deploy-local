#!/usr/bin/env python3
"""Apply the "inline base64 image" patch to plugins/platforms/simplex/adapter.py.

SimpleX mobile clients deliver photos as inline base64 (``msgContent.type="image"``,
``msgContent.image="data:image/jpg;base64,..."``) with no ``chatItem.file`` field,
so the stock adapter drops the pixels and the agent sees only the caption.
This patch extracts the data URL into ``media_urls`` so the gateway's vision
pipeline (``image_source._resolve_data_url`` -> vision model) analyzes it.

Idempotent via a marker comment; re-apply with ``--force`` after every
``hermes update``.

Usage::

    python3 patches/simplex_inline_image.py [--status] [--force]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

AGENT_DIR = Path(os.path.expanduser("~/.hermes/hermes-agent"))
TARGET = AGENT_DIR / "plugins" / "platforms" / "simplex" / "adapter.py"
MARKER = "# Inline images: SimpleX mobile clients deliver photos as base64 data"

BLOCK = '''        # Inline images: SimpleX mobile clients deliver photos as base64 data
        # URLs in msgContent.image with no chatItem.file sibling. Surface them
        # through the same media slot so the gateway's vision pipeline
        # (image_source._resolve_data_url) can analyze them.
        if not media_urls and msg_type_str == "image":
            inline = msg_content.get("image", "")
            if isinstance(inline, str) and inline.startswith("data:image/"):
                mime = inline[len("data:"):].split(";", 1)[0] or "image/jpeg"
                media_urls.append(inline)
                media_types.append(mime)

        if media_urls:
            logger.info(
                "SimpleX: message from %s in %s carries %d media attachment(s)",
                _redact_id(sender_id),
                chat_id[:20],
                len(media_urls),
            )
'''


def _read() -> str:
    return TARGET.read_text(encoding="utf-8")


def status() -> bool:
    return MARKER in _read()


def apply(force: bool = False) -> None:
    src = _read()
    if status() and not force:
        print("already applied (inline-image block present); use --force to re-apply")
        return
    if status() and force:
        raise SystemExit(
            "refusing to re-apply: patch is already present. Revert it manually "
            "or reinstall hermes-agent first."
        )

    needle = '                    media_types.append("application/octet-stream")\n'
    if needle not in src:
        raise SystemExit(
            "anchor not found: 'media_types.append(\"application/octet-stream\")'"
        )
    src = src.replace(needle, needle + BLOCK, 1)
    TARGET.write_text(src, encoding="utf-8")
    print(f"applied {TARGET}")


def main() -> None:
    parser = argparse.ArgumentParser(description="apply simplex inline-image patch")
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
