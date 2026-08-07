#!/usr/bin/env bash
# Throwaway Forge Ledger for clicking around in. Nothing survives it.
#
#   ./test-instance.sh          build if needed, run in the foreground
#   ./test-instance.sh -d       run detached
#   ./test-instance.sh --build  force a rebuild first
#   ./test-instance.sh stop     stop it
#
# /library and /data are tmpfs, so the sample library lives in RAM and is gone
# the moment the container stops. There are no bind mounts, so this can never
# reach your real projects.

set -euo pipefail

IMAGE="forge-ledger:test"
NAME="forge-ledger-test"
PORT="${FORGE_TEST_PORT:-8001}"
cd "$(dirname "$0")"

# Apple's `container` on macOS, Docker elsewhere. Both take the same flags here.
if command -v container >/dev/null 2>&1; then
    CLI=container
elif command -v docker >/dev/null 2>&1; then
    CLI=docker
else
    echo "Need either Apple's 'container' or 'docker' on PATH." >&2
    exit 1
fi

detach=""
force_build=""
for arg in "$@"; do
    case "$arg" in
        stop)
            "$CLI" rm -f "$NAME" >/dev/null 2>&1 || true
            echo "Stopped $NAME. The sample library is gone."
            exit 0
            ;;
        -d|--detach) detach="-d" ;;
        --build) force_build="yes" ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

if [ -n "$force_build" ] || ! "$CLI" image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "==> Building $IMAGE"
    "$CLI" build -t "$IMAGE" .
fi

# A leftover from a previous run would otherwise block the name.
"$CLI" rm -f "$NAME" >/dev/null 2>&1 || true

echo "==> Starting on http://localhost:${PORT}"
[ -z "$detach" ] && echo "    Ctrl-C to stop. Everything in it disappears."

exec "$CLI" run --rm $detach \
    --name "$NAME" \
    -p "${PORT}:8000" \
    --tmpfs /library \
    --tmpfs /data \
    -e FORGE_DEMO_SEED=true \
    -e FORGE_WATCH_DEBOUNCE_SECONDS=0.5 \
    -e FORGE_OLLAMA_URL="${FORGE_OLLAMA_URL:-}" \
    "$IMAGE"
