#!/usr/bin/env bash
# Dev-only launcher for the backend container: gunicorn plus the OIDC loopback
# forwarder, supervised together.
#
# The forwarder is not optional while the compose `auth` profile is running --
# without it the backend reaches Authentik under a second hostname and forks one
# login into two Seizu users (AUTH-001). Backgrounding it and exec'ing gunicorn
# would let it die while the container kept passing its port-8080 healthcheck,
# recreating exactly the silent auth breakage AUTH-002 exists to remove. So both
# processes are supervised here: whichever exits first takes the container with
# it, and a forwarder that cannot bind fails the container at startup.
set -uo pipefail

SEIZU_DIR=/home/seizu/seizu

# Run from /run/seizu (tmpfs) so gunicorn's arbiter control socket is not
# created in the bind-mounted project directory, where the seizu-node watcher
# trips over it.
cd /run/seizu || exit 1

pids=()

terminate() {
    if [ ${#pids[@]} -gt 0 ]; then
        kill -TERM "${pids[@]}" 2>/dev/null
    fi
}

# Docker signals PID 1, which is this shell; without forwarding, gunicorn would
# never see SIGTERM and every `make down` would wait out the kill timeout.
trap terminate TERM INT

if [ -n "${DEV_OIDC_LOOPBACK_TARGET:-}" ]; then
    python "$SEIZU_DIR/scripts/dev_oidc_loopback.py" &
    pids+=($!)
fi

gunicorn --config "$SEIZU_DIR/gunicorn.conf" reporting.asgi:application \
    --reload --workers=2 -k uvicorn.workers.UvicornWorker \
    --access-logfile=- --error-logfile=- &
pids+=($!)

wait -n
status=$?
terminate
wait
exit "$status"
