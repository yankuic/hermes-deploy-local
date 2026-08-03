#!/usr/bin/env python3
"""Apply the "structured /_send" DM patch to plugins/platforms/simplex/adapter.py.

simplex-chat treats bare ``@<id>`` as a display-name lookup, so outbound DMs
addressed as ``@4 Hello`` fail with ``contactNotFound`` and are silently
dropped (fire-and-forget WS send). The structured ``/_send @<id> json [...]``
form addresses by numeric contact ID.

Replaces the bare ``cmd_str = f"@{chat_id} {content}"`` form in ``send()``
and ``_standalone_send()``. Idempotent via a marker string; re-apply with
``--force`` after every ``hermes update``.

Usage::

    python3 patches/simplex_dm_send.py [--status] [--force]
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

AGENT_DIR = Path(os.path.expanduser("~/.hermes/hermes-agent"))
TARGET = AGENT_DIR / "plugins" / "platforms" / "simplex" / "adapter.py"
MARKER = "/_send {prefix}{target} json {composed}"

# Bare form in send() / _standalone_send() (content vs message).
BARE_RE = re.compile(
    r'^(?P<indent>[ \t]*)cmd_str\s*=\s*f"@\{chat_id\}\s*\{\s*(?P<var>content|message)\s*\}"',
    re.M,
)

STRUCTURED = '''# Structured form using /_send: addresses by numeric ID (simplex-chat
# treats bare @<id> as a display-name lookup, not a contact ID).
# json.dumps escapes newlines and special chars correctly.
composed = json.dumps(
    [{{"msgContent": {{"type": "text", "text": {var}}}}}]
)
prefix = "#" if chat_id.startswith("group:") else "@"
target = chat_id[6:] if chat_id.startswith("group:") else chat_id
cmd_str = f"/_send {{prefix}}{{target}} json {{composed}}"'''


def _read() -> str:
    return TARGET.read_text(encoding="utf-8")


def status() -> bool:
    return MARKER in _read()


def apply(force: bool = False) -> None:
    src = _read()
    if status() and not force:
        print("already applied (structured /_send present); use --force to re-apply")
        return
    if status() and force:
        raise SystemExit(
            "refusing to re-apply: patch is already present. Revert it manually "
            "or reinstall hermes-agent first."
        )

    matches = list(BARE_RE.finditer(src))
    if not matches:
        raise SystemExit(
            "no bare-form 'cmd_str = f\"@{chat_id} ...\"' found; the patch may "
            "already be applied or upstream changed the code"
        )
    for m in reversed(matches):
        block = STRUCTURED.format(var=m.group("var"))
        block = "\n".join(
            (m.group("indent") + ln) if ln else ln for ln in block.split("\n")
        )
        src = src[: m.start()] + block + src[m.end():]
    TARGET.write_text(src, encoding="utf-8")
    print(f"applied {TARGET} ({len(matches)} replacement(s))")


def main() -> None:
    parser = argparse.ArgumentParser(description="apply simplex /_send DM patch")
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
