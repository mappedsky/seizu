#!/bin/sh
set -eu

loopback_target="${DEV_OIDC_LOOPBACK_TARGET:?DEV_OIDC_LOOPBACK_TARGET is required}"

# One dual-stack listener means Go's preference for ::1 or 127.0.0.1 cannot
# change the Authentik authority used for discovery and token exchange.
socat TCP6-LISTEN:9000,ipv6only=0,reuseaddr,fork "TCP:${loopback_target}" &
forwarder_pid=$!

"$@" &
proxy_pid=$!

stop_children() {
  kill "$proxy_pid" "$forwarder_pid" 2>/dev/null || true
  wait "$proxy_pid" "$forwarder_pid" 2>/dev/null || true
}

trap 'stop_children; exit 143' TERM
trap 'stop_children; exit 130' INT

while kill -0 "$proxy_pid" 2>/dev/null && kill -0 "$forwarder_pid" 2>/dev/null
do
  sleep 1
done

status=1
if ! kill -0 "$proxy_pid" 2>/dev/null
then
  wait "$proxy_pid" || status=$?
else
  wait "$forwarder_pid" || status=$?
fi

stop_children
exit "$status"
