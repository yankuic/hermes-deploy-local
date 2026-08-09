#!/usr/bin/env python3
"""Make the Linux desktop launcher entry exec the wrapper script.

``hermes desktop`` (and ``hermes update``) reinstalls
``~/.local/share/applications/hermes.desktop`` on every run via
``install_desktop_entry()`` -> ``resolve_exec_command()`` in
``hermes_cli/linux_desktop_entry.py``. That function writes ``Exec=<resolved
hermes bin> desktop`` (the venv python launcher), which BREAKS launching from
the GNOME app dashboard: GNOME uses a minimal PATH from ``/etc/environment``
that excludes ``~/.local/bin`` and the nvm node dir, so ``hermes desktop``
fails its npm check and exits silently before Electron starts.

The wrapper ``~/.local/bin/hermes-desktop`` prepends
``~/.nvm/versions/node/<ver>/bin`` and ``~/.local/bin`` to PATH before running
``hermes desktop``. This patch makes ``resolve_exec_command()`` prefer that
wrapper when it exists and is executable, so every reinstall keeps the
working entry (regression seen 2026-08-09 after a rebuild clobbered it back
to the venv Exec). See memory-bank/hermes-config.md "Desktop App Launcher".

Idempotent via a marker comment; re-apply after every ``hermes update``.

Usage::

    python3 patches/desktop_launcher_wrapper.py [--status] [--force]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

AGENT_DIR = Path(os.environ.get("HERMES_AGENT_DIR") or os.path.expanduser("~/.hermes/hermes-agent"))
TARGET = AGENT_DIR / "hermes_cli" / "linux_desktop_entry.py"
MARKER = "Prefer the user's wrapper script when present"

ANCHOR = """    from hermes_cli.relaunch import resolve_hermes_bin

    bin_path = resolve_hermes_bin()
"""

INSERT = """    from hermes_cli.relaunch import resolve_hermes_bin

    # Prefer the user's wrapper script when present: GNOME launches apps with
    # a minimal PATH (/etc/environment) that excludes ~/.local/bin and the nvm
    # node dir, so `hermes desktop` fails its npm check before Electron
    # starts. The wrapper prepends those dirs to PATH (see
    # memory-bank/hermes-config.md "Desktop App Launcher").
    wrapper = Path.home() / ".local" / "bin" / "hermes-desktop"
    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        return _quote_exec_arg(str(wrapper))

    bin_path = resolve_hermes_bin()
"""


def _read() -> str:
    return TARGET.read_text(encoding="utf-8")


def status() -> bool:
    return MARKER in _read()


def apply(force: bool = False) -> None:
    src = _read()
    if status():
        if not force:
            print("already applied (wrapper-preferred resolve_exec_command); use --force to re-apply")
            return
        raise SystemExit(
            "refusing to re-apply: patch is already present. Revert it manually "
            "or reinstall hermes-agent first."
        )
    if ANCHOR not in src:
        raise SystemExit(
            "anchor 'from hermes_cli.relaunch import resolve_hermes_bin' not found; "
            "upstream changed resolve_exec_command()"
        )
    TARGET.write_text(src.replace(ANCHOR, INSERT, 1), encoding="utf-8")
    print(f"applied {TARGET}")


def main() -> None:
    parser = argparse.ArgumentParser(description="apply desktop launcher wrapper patch")
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
