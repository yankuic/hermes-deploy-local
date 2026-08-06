#!/usr/bin/env python3
"""Apply/revert the profile-isolation preview patch.

This is a LOCAL PREVIEW mirroring upstream PR #77605
("fix(profiles): add strict host file-tool scope") semantics — an opt-in
``agent.profile_scope: strict`` host-side file-tool boundary for named
profiles. It patches two files in the hermes-agent install tree:

* ``agent/file_safety.py``    — strict scope classification + read/write guards
* ``tools/file_tools.py``     — move the read guard BEFORE structured-document
                                extraction so .docx/.pdf/.xlsx can't bypass it

It must be re-applied after every `hermes update` (the updater rewrites the
install tree). Run::

    python3 ~/.hermes/patches/apply-profile-isolation.py          # apply
    python3 ~/.hermes/patches/apply-profile-isolation.py --status  # inspect
    python3 ~/.hermes/patches/apply-profile-isolation.py --revert  # restore
    python3 ~/.hermes/patches/apply-profile-isolation.py --force   # re-apply after update

Behavior when applied (only when the active profile's config.yaml sets
``agent.profile_scope: strict``; default ``none`` = unchanged behavior):

* Reads/writes under the active profile's own ``HERMES_HOME`` tree: allowed.
* Paths listed in ``agent.profile_scope_allow`` (absolute Hermes-root paths,
  e.g. a deliberately shared cache): allowed.
* Everything else under the Hermes root — default-profile state
  (config.yaml, memories/, sessions/, cron/, ...), sibling profiles, and the
  shared ``<root>/cache`` — is denied. ``cross_profile=True`` cannot bypass.
* Paths outside ``<root>`` (vault, home, /tmp, ...): not covered, allowed.
* The default profile (HERMES_HOME == root) with ``strict`` owns the root but
  cannot enter ``profiles/`` trees.

Idempotent via a marker comment; hash-guarded so an updated (rewritten) file
refuses patching unless ``--force`` is given. Backups are written next to the
install as ``<file>.pre-profile-isolation``.
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
META_NAME = "profile-isolation.json"

HELPER = '''

# --- profile-isolation patch (local preview) ---
# Strict host file-tool scope (mirrors upstream PR #77605):
# agent.profile_scope: strict + agent.profile_scope_allow
def _local_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _local_profile_scope_settings() -> tuple:
    """Return (mode, allowed_paths) for the active profile's strict scope.

    Reads ``agent.profile_scope`` / ``agent.profile_scope_allow`` from the
    active profile's config.yaml. ``load_config_readonly()`` is cached by
    config path. Any config/load error falls back to ``("none", ())`` so a
    broken config never breaks file tools.
    """
    try:
        from hermes_cli.config import load_config_readonly
        agent_cfg = load_config_readonly().get("agent", {})
        if not isinstance(agent_cfg, dict) or agent_cfg.get("profile_scope") != "strict":
            return "none", ()
        raw_allow = agent_cfg.get("profile_scope_allow", [])
        if not isinstance(raw_allow, (list, tuple)):
            return "strict", ()
    except Exception:
        return "none", ()
    allowed = []
    for raw_path in raw_allow:
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        try:
            candidate = Path(raw_path).expanduser()
            if candidate.is_absolute():
                allowed.append(candidate.resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return "strict", tuple(allowed)


def _local_profile_scope_violation(path) -> Optional[dict]:
    """Return a denial dict for an out-of-scope Hermes path, else None.

    Active only when the active profile sets ``agent.profile_scope: strict``.
    In scope: the active profile's own tree and ``profile_scope_allow`` paths.
    Everything else under the Hermes root — default-profile state, sibling
    profiles, and the shared ``<root>/cache`` — is denied. Paths outside the
    Hermes root (vault, home, /tmp, ...) are not covered.
    """
    mode, allowed_paths = _local_profile_scope_settings()
    if mode != "strict":
        return None
    try:
        root_real = _hermes_root_path().resolve()
        home_real = _hermes_home_path().resolve()
        target = Path(os.path.expanduser(str(path))).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    for allowed in allowed_paths:
        if _local_is_within(target, allowed):
            return None
    profiles_dir = root_real / "profiles"
    try:
        active_rel = home_real.relative_to(profiles_dir)
    except ValueError:
        # Default profile owns the root; strict blocks named-profile trees.
        if _local_is_within(target, profiles_dir):
            try:
                trel = target.relative_to(profiles_dir)
                target_profile = trel.parts[0] if trel.parts else "default"
            except ValueError:
                target_profile = "default"
            return {
                "active_profile": "default",
                "target_profile": target_profile,
                "target_path": str(target),
            }
        return None
    if len(active_rel.parts) != 1:
        return None
    if _local_is_within(target, home_real):
        return None  # active profile's own tree
    if not _local_is_within(target, root_real):
        return None  # outside Hermes root — not covered
    target_profile = "default"
    try:
        trel = target.relative_to(profiles_dir)
        if trel.parts:
            target_profile = trel.parts[0]
    except ValueError:
        pass
    return {
        "active_profile": active_rel.parts[0],
        "target_profile": target_profile,
        "target_path": str(target),
    }


def _local_profile_scope_error(path, *, verb) -> Optional[str]:
    info = _local_profile_scope_violation(path)
    if info is None:
        return None
    return (
        f"{verb} denied by agent.profile_scope: strict: {info['target_path']} "
        f"belongs to Hermes profile {info['target_profile']!r}, but the "
        f"active profile is {info['active_profile']!r}. Switch profiles or "
        "add a deliberate shared path to agent.profile_scope_allow."
    )

'''

READ_GUARD = '''    profile_scope_error = _local_profile_scope_error(str(resolved), verb="Read")
    if profile_scope_error:
        return profile_scope_error

'''

WRITE_GUARD = '''    if _local_profile_scope_violation(str(resolved)) is not None:
        return "profile_scope"

'''

DENIED_MSG = '''    if denial == "profile_scope":
        return _local_profile_scope_error(path, verb=verb)

'''

# file_tools.py: early read guard (before structured-document extraction)
FT_INSERT = '''
        # Hermes internal path guard — before structured-document extraction
        # so .docx/.pdf/.xlsx cannot bypass profile/credential rules
        # (mirrors upstream PR #77605).
        # --- profile-isolation patch (local preview) ---
        block_error = get_read_block_error(str(_resolved))
        if block_error:
            return tool_error(block_error)
'''

# file_tools.py: the old (post-extraction) guard, removed as redundant.
FT_REMOVE_OLD = '''        # ── Hermes internal path guard ────────────────────────────────
        # Prevent prompt injection via catalog or hub metadata files,
        # and block credential stores under HERMES_HOME.  Pass the
        # already-resolved path so a relative-path read against
        # TERMINAL_CWD == HERMES_HOME (e.g. "auth.json") still hits the
        # denylist — get_read_block_error's own resolve() runs against
        # the Python process cwd, which can differ.
        block_error = get_read_block_error(str(_resolved))
        if block_error:
            return tool_error(block_error)

'''

TARGETS = [
    {
        "rel": "agent/file_safety.py",
        "backup": "file_safety.py.pre-profile-isolation",
        "patches": [
            ("helper", HELPER, 'def build_write_denied_paths(home: str)', "insert_before"),
            ("read guard", READ_GUARD, '    resolved = Path(path).expanduser().resolve()\n\n', "insert_after"),
            ("write guard", WRITE_GUARD,
             '    home = os.path.realpath(os.path.expanduser("~"))\n    resolved = os.path.realpath(os.path.expanduser(str(path)))\n',
             "insert_after"),
            ("denied message", DENIED_MSG, '    if denial is None:\n        return None\n', "insert_after"),
        ],
    },
    {
        "rel": "tools/file_tools.py",
        "backup": "file_tools.py.pre-profile-isolation",
        "patches": [
            ("read guard early", FT_INSERT, '        _resolved = _resolve_path_for_task(path, task_id)\n', "insert_after"),
            ("read guard remove", "", FT_REMOVE_OLD, "replace"),
        ],
    },
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def apply_target(target: Path, patches) -> bool:
    src = read_text(target)
    if MARKER in src:
        print(f"already applied — nothing to do: {target}")
        return False

    # Validate every anchor against the pristine source before mutating, so a
    # failed patch never leaves a partially-edited file.
    for name, insertion, anchor, kind in patches:
        if src.count(anchor) != 1:
            print(f"error: anchor for '{name}' found {src.count(anchor)} times (expected 1)")
            print("the install tree may have changed — re-run with --force to re-baseline")
            return False

    for name, insertion, anchor, kind in patches:
        if kind == "replace":
            src = src.replace(anchor, insertion, 1)
        elif kind == "insert_before":
            src = src.replace(anchor, insertion + anchor, 1)
        else:
            src = src.replace(anchor, anchor + insertion, 1)

    target.write_text(src, encoding="utf-8")
    print(f"patched: {target}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agent-dir", default=str(DEFAULT_AGENT_DIR),
                    help="hermes-agent install dir (default: %(default)s)")
    ap.add_argument("--status", action="store_true", help="show patch state")
    ap.add_argument("--revert", action="store_true", help="restore pre-patch backups")
    ap.add_argument("--force", action="store_true",
                    help="re-apply even if baseline hash no longer matches (after update)")
    args = ap.parse_args()

    agent_dir = Path(args.agent_dir).resolve()
    meta = PATCHES_DIR / META_NAME

    state: dict = {}
    if meta.is_file():
        try:
            state = json.loads(meta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    file_states = state.get("files", {})

    targets = []
    for t in TARGETS:
        target = agent_dir / t["rel"]
        if not target.is_file():
            print(f"error: {target} not found")
            return 1
        backup = PATCHES_DIR / t["backup"]
        targets.append({"target": target, "backup": backup, "patches": t["patches"],
                        "rel": t["rel"], "state": file_states.get(t["rel"], {})})

    if args.status:
        for t in targets:
            patched = MARKER in read_text(t["target"])
            baseline = t["state"].get("baseline_sha256")
            cur = sha256_file(t["target"])
            print(f"target : {t['target']}")
            print(f"patched: {patched}")
            print(f"backup : {t['backup'].exists()}")
            if baseline:
                print(f"baseline ok: {baseline == cur}")
            print()
        return 0

    if args.revert:
        for t in targets:
            if t["backup"].is_file():
                t["target"].write_bytes(t["backup"].read_bytes())
                t["backup"].unlink()
                print(f"reverted: {t['target']}")
            else:
                print(f"no backup to restore: {t['target']}")
        meta.unlink(missing_ok=True)
        print("metadata removed:", meta)
        return 0

    any_skipped = False
    for t in targets:
        if MARKER in read_text(t["target"]):
            print(f"already applied — nothing to do: {t['target']} "
                  "(--revert to undo, --force to re-baseline)")
            any_skipped = True
            continue

        baseline = t["state"].get("baseline_sha256")
        current_hash = sha256_file(t["target"])
        if baseline is not None and baseline != current_hash and not args.force:
            print(f"error: {t['rel']} differs from the baseline this patch was built against")
            print("(hermes update rewrites the tree). Re-run with --force to re-apply and re-baseline.")
            return 1

        if not t["backup"].is_file() or (args.force and baseline != current_hash):
            t["backup"].write_bytes(t["target"].read_bytes())
            print(f"backup written: {t['backup']}")

        if not apply_target(t["target"], t["patches"]):
            return 1
        file_states[t["rel"]] = {"baseline_sha256": sha256_file(t["target"]),
                                 "backup": t["backup"].name}

    if any_skipped:
        print("NOTE: some files were already patched; re-run with --revert first to reset.")

    meta.write_text(json.dumps({"marker": MARKER, "files": file_states}, indent=2),
                    encoding="utf-8")
    print("metadata written:", meta)
    print("NOTE: restart affected gateways/serve processes "
          "(systemctl --user restart hermes-gateway@researcher.service).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
