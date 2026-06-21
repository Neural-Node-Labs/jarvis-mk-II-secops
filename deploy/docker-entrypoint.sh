#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Why this exists
# ─────────────────────────────────────────────────────────────────────────────
# The Dockerfile does `chown -R jarvis:jarvis /app` at BUILD time. That only
# affects the image's own filesystem layers. /app/data, /app/experienced, and
# /app/output are bind-mounted from the host in docker-compose.yml, and bind
# mounts are attached at CONTAINER START — after the image is already built —
# so they completely override whatever ownership those paths had in the image.
#
# If the host-side folders (./jarvis/data, ./jarvis/experienced, ./jarvis/output)
# don't already exist, Docker's root-owned daemon creates them on first run,
# owned by root:root. The FastAPI app itself runs as the unprivileged `jarvis`
# user (see supervisord's per-program `user=` directive — nginx needs to stay
# root to bind port 80, which is why Dockerfile's own `USER jarvis` is left
# commented out, but the app process underneath it does not run as root).
# Net effect: "workspace gets created owned by root, the agent can't write to
# it" — exactly the bug this script fixes.
#
# This script runs as root (it's the container ENTRYPOINT, before supervisord
# starts), fixes ownership of every directory the app needs to write into,
# and then hands off to the real command. It's idempotent — safe to run on
# every container start, including ones where the directories already have
# the right owner.
# ─────────────────────────────────────────────────────────────────────────────
set -e

TARGET_UID="${PUID:-1001}"
TARGET_GID="${PGID:-1001}"

CURRENT_UID="$(id -u jarvis 2>/dev/null || echo "")"
CURRENT_GID="$(id -g jarvis 2>/dev/null || echo "")"
if [ "$CURRENT_UID" != "$TARGET_UID" ] || [ "$CURRENT_GID" != "$TARGET_GID" ]; then
    usermod -u "$TARGET_UID" jarvis 2>/dev/null || true
    groupmod -g "$TARGET_GID" jarvis 2>/dev/null || true
    # Re-claim files that were owned by the old uid/gid before we changed it
    find /app -xdev \( -uid "$CURRENT_UID" -o -gid "$CURRENT_GID" \) -exec chown jarvis:jarvis {} + 2>/dev/null || true
fi

# /app/data, /app/experienced, /app/output are bind-mounted (docker-compose.yml).
# JARVIS_WORKSPACE_ROOT may ALSO point at a bind-mounted path (e.g. set to
# /app/output instead of the ephemeral default of /tmp) for persistent agent
# workspaces — include it too if it's set.
WRITABLE_DIRS="/app/data /app/experienced /app/output"
if [ -n "$JARVIS_WORKSPACE_ROOT" ]; then
    WRITABLE_DIRS="$WRITABLE_DIRS $JARVIS_WORKSPACE_ROOT"
fi

for d in $WRITABLE_DIRS; do
    mkdir -p "$d"
    chown -R jarvis:jarvis "$d" 2>/dev/null || {
        echo "[entrypoint] WARNING: could not chown $d — the agent may not be able to write to it." >&2
    }
done

exec "$@"
