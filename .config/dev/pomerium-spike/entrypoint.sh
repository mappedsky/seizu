#!/bin/sh
# Generates the spike's local CA and wildcard certificate on first start, then
# runs Pomerium supervised beside a localhost:9000 -> Authentik forwarder.
#
# Pomerium marks every cookie Secure, so the spike must be served over HTTPS.
# The CA lands in /certs (bind-mounted from .compose/pomerium-spike) so the
# host-side client can trust it with --cafile, and a browser can import it.
set -eu

certs=/certs
loopback_target="${DEV_OIDC_LOOPBACK_TARGET:?DEV_OIDC_LOOPBACK_TARGET is required}"

if [ ! -s "$certs/server.pem" ] || [ ! -s "$certs/server-key.pem" ] || [ ! -s "$certs/ca.pem" ]
then
  umask 077
  openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
    -subj "/CN=Seizu Pomerium spike CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$certs/ca-key.pem" -out "$certs/ca.pem" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes \
    -subj "/CN=*.localhost.pomerium.io" \
    -keyout "$certs/server-key.pem" -out "$certs/server.csr" 2>/dev/null
  printf '%s\n' \
    "subjectAltName=DNS:*.localhost.pomerium.io" \
    "extendedKeyUsage=serverAuth" \
    "basicConstraints=CA:FALSE" > "$certs/server.ext"
  openssl x509 -req -days 30 -in "$certs/server.csr" \
    -CA "$certs/ca.pem" -CAkey "$certs/ca-key.pem" -CAcreateserial \
    -extfile "$certs/server.ext" -out "$certs/server.pem" 2>/dev/null
  rm -f "$certs/server.csr" "$certs/server.ext"
  chmod 644 "$certs/ca.pem" "$certs/server.pem"
  echo "pomerium-spike: generated local CA at .compose/pomerium-spike/ca.pem"
fi

# One dual-stack listener so Go's choice of ::1 or 127.0.0.1 cannot change the
# Authentik authority used for discovery and token exchange (AUTH-001/002).
socat TCP6-LISTEN:9000,ipv6only=0,reuseaddr,fork "TCP:${loopback_target}" &
forwarder_pid=$!

"$@" &
server_pid=$!

stop_children() {
  kill "$server_pid" "$forwarder_pid" 2>/dev/null || true
  wait "$server_pid" "$forwarder_pid" 2>/dev/null || true
}

trap 'stop_children; exit 143' TERM
trap 'stop_children; exit 130' INT

while kill -0 "$server_pid" 2>/dev/null && kill -0 "$forwarder_pid" 2>/dev/null
do
  sleep 1
done

status=1
if ! kill -0 "$server_pid" 2>/dev/null
then
  wait "$server_pid" || status=$?
else
  wait "$forwarder_pid" || status=$?
fi

stop_children
exit "$status"
