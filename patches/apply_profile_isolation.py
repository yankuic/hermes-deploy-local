#!/usr/bin/env python3
"""Apply/revert the profile-isolation preview patch to agent/file_safety.py.

This is a LOCAL PREVIEW of an upstream feature request (per-profile
filesystem isolation). It must be re-applied after every `hermes update`
(the updater rewrites the install tree). Run::

    python3 ~/.hermes/patches/apply-profile-isolation.py          # apply
    python3 ~/.hermes/patches/apply-profile-isolation.py --status  # inspect
    python3 ~/.hermes/patches/apply-profile-isolation.py --revert  # restore
    python3 ~/.hermes/patches/apply-profile-isolation.py --force   # re-apply after update

Behavior when applied:

* Only active when the process runs under a profile (HERMES_HOME under
  ``<root>/profiles/<name>/``). The default profile is byte-identical.
* Reads: paths under ``<root>/profiles/<other>/`` or top-level default
  profile state (config.yaml, memories/, sessions/, cron/, skills/,
  plugins/, sandboxes/, kanban/, state.db, ...) are denied.
* Writes: the same paths are hard-denied in the write-deny classification,
  which runs before the ``cross_profile=True`` soft-guard — the override
  is ignored.
* Still allowed: the active profile's own tree, shared ``<root>/cache/``,
  and everything outside ``<root>`` (e.g. the Obsidian vault).

Idempotent via a marker comment; hash-guarded so an updated (rewritten)
file refuses patching unless ``--force`` is given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

MARKER = "# --- profile-isolation patch (local preview) ---"

DEFAULT_AGENT_DIR = Path(os.path.expanduser("~/.hermes/hermes-agent"))
PATCHES_DIR = Path(os.path.expanduser("~/.hermes/patches"))
BACKUP_NAME = "file_safety.py.pre-profile-isolation"
META_NAME = "profile-isolation.json"

HELPER = '''

# --- profile-isolation patch (local preview) ---
# Profile-scope enforcement
def _profile_scope_violation(path: str) -> Optional[str]:
    """Return a denial reason for an out-of-scope path, or None.

    Only active when the active profile is NOT the default profile.
    In scope: the active profile's own tree and the shared ``<root>/cache/``.
    Everything else under ``<root>`` — sibling profiles and top-level
    default-profile state (config.yaml, memories/, sessions/, cron/,
    skills/, plugins/, sandboxes/, kanban/, state.db, ...) — is out of
    scope. Paths outside ``<root>`` (vault, home, /tmp, ...) are not
    covered by this guard.
    """
    try:
        root_real = _hermes_root_path().resolve()
        home_real = _hermes_home_path().resolve()
        target = Path(os.path.expanduser(str(path))).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        home_real.relative_to(root_real / "profiles")
    except ValueError:
        return None  # default profile — no scope enforcement
    try:
        target.relative_to(home_real)
        return None  # active profile's own tree
    except ValueError:
        pass
    try:
        rel = target.relative_to(root_real)
    except ValueError:
        return None  # outside Hermes root — not covered
    if rel.parts and rel.parts[0] == "cache":
        return None  # shared cache (documents/images/web) — in scope
    return rel.as_posix()

'''

READ_GUARD = '''    scope = _profile_scope_violation(str(resolved))
    if scope is not None:
        return (
            f"Access denied: {path} is outside this profile's scope "
            f"(target: <hermes-root>/{scope}). Only your own profile "
            "directory and shared caches are accessible."
        )

'''

WRITE_GUARD = '''    scope = _profile_scope_violation(str(resolved))
    if scope is not None:
        return "profile_scope"

'''

DENIED_MSG = '''    if denial == "profile_scope":
        return (
            f"{verb} denied: '{path}' is outside this profile's scope. "
            "Only your own profile directory and shared caches are "
            "accessible; other profiles' data is off-limits "
            "(cross_profile=True does not override this)."
        )

'''


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def apply_patch(target: Path) -> None:
    src = read_text(target)
    if MARKER in src:
        print("already applied — nothing to do")
        return

    patches = [
        ("helper", HELPER, 'def build_write_denied_paths(home: str)'),
        ("read guard", READ_GUARD, '    resolved = Path(path).expanduser().resolve()\n\n'),
        ("write guard", WRITE_GUARD, '    home = os.path.realpath(os.path.expanduser("~"))\n    resolved = os.path.realpath(os.path.expanduser(str(path)))\n'),
        ("denied message", DENIED_MSG, '    if denial is None:\n        return None\n'),
    ]
    for name, insertion, anchor in patches:
        if src.count(anchor) != 1:
            print(f"error: anchor for '{name}' found {src.count(anchor)} times (expected 1)")
            print("the install tree may have changed — re-run with --force to re-baseline")
            sys.exit(1)
        if name == "helper":
            src = src.replace(anchor, insertion + anchor, 1)
        else:
            src = src.replace(anchor, anchor + insertion, 1)

    target.write_text(src, encoding="utf-8")
    print(f"patched: {target}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agent-dir", default=str(DEFAULT_AGENT_DIR),
                    help="hermes-agent install dir (default: %(default)s)")
    ap.add_argument("--status", action="store_true", help="show patch state")
    ap.add_argument("--revert", action="store_true", help="restore pre-patch backup")
    ap.add_argument("--force", action="store_true",
                    help="re-apply even if baseline hash no longer matches (after update)")
    args = ap.parse_args()

    agent_dir = Path(args.agent_dir).resolve()
    target = agent_dir / "agent" / "file_safety.py"
    backup = PATCHES_DIR / BACKUP_NAME
    meta = PATCHES_DIR / META_NAME

    if not target.is_file():
        print(f"error: {target} not found")
        return 1

    current_hash = sha256_file(target)
    state: dict = {}
    if meta.is_file():
        try:
            state = json.loads(meta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}

    patched = MARKER in read_text(target)

    if args.status:
        print(f"target : {target}")
        print(f"patched: {patched}")
        print(f"backup : {backup.exists()}")
        if state.get("baseline_sha256"):
            print(f"baseline ok: {state['baseline_sha256'] == current_hash}")
        return 0

    if args.revert:
        if not backup.is_file():
            print("error: no backup to restore")
            return 1
        target.write_bytes(backup.read_bytes())
        backup.unlink()
        meta.unlink(missing_ok=True)
        print(f"reverted: {target}")
        return 0

    if patched:
        print("already applied — nothing to do (--revert to undo, --force to re-baseline)")
        return 0

    baseline = state.get("baseline_sha256")
    if baseline is not None and baseline != current_hash and not args.force:
        print("error: install tree differs from the baseline this patch was built against")
        print("(hermes update rewrites the tree). Re-run with --force to re-apply and re-baseline.")
        return 1

    if not backup.is_file() or (args.force and baseline != current_hash):
        backup.write_bytes(target.read_bytes())
        print(f"backup written: {backup}")

    apply_patch(target)

    meta.write_text(json.dumps({
        "baseline_sha256": sha256_file(target),
        "marker": MARKER,
    }, indent=2), encoding="utf-8")
    print("metadata written:", meta)
    print("NOTE: restart the gateway (systemctl --user restart hermes-gateway.service)")
    print("      and recreate sandbox containers if mounts changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
