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
run simplex_dm_send.py "$@"
run simplex_inline_image.py "$@"
run apply-profile-isolation.py "$@"

exit $STATUS
