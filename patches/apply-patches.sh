#!/usr/bin/env bash
# Apply all local hermes-agent patches. Idempotent: each patch script skips
# when its marker is already present. Re-run after every `hermes update`.
# Usage:
#   ./apply-patches.sh            # apply everything
#   ./apply-patches.sh --status   # report applied/not-applied for each
set -uo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
STATUS=0

run() {
    name="$1"; shift
    if [ "${1:-}" = "--status" ]; then
        echo "[status] $name: $($PY "$name" --status)"
        return 0
    fi
    if ! $PY "$name"; then
        echo "[FAILED] $name" >&2
        STATUS=1
    fi
}

run image_source_output_mounts.py "$@"
# simplex_dm_send.py retired 2026-08-09: upstream main now natively uses the
# structured `/_send @<id> json [...]` form in adapter.py send() and
# _standalone_send(), so the patch has no bare-form target left.
# run simplex_dm_send.py "$@"
run simplex_inline_image.py "$@"
run apply-profile-isolation.py "$@"
run desktop_slash_live_gateway.py "$@"
run desktop_launcher_wrapper.py "$@"

exit $STATUS
