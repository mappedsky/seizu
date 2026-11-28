#!/bin/sh
# Generates the local gateway's CA and wildcard certificate, then runs Pomerium
# supervised beside a localhost:9000 -> Authentik forwarder.
#
# Pomerium serves its MCP OAuth endpoints over HTTPS only, so the gateway needs
# a certificate. The CA lands in /certs (bind-mounted from
# .compose/external-mcp-gateway), where Seizu trusts it through
# MCP_EXTERNAL_CA_FILE. Browsers are not meant to trust it; users accept the
# certificate warning while connecting (AUTH-004).
#
# The CA can only vouch for *.localhost.pomerium.io (name constraints), and its
# private key is discarded once the server certificate is signed. Without the
# key nothing can be renewed: a CA from before the constraints, or a
# certificate within 30 days of expiry, is replaced by a new pair.
set -eu

certs=/certs
loopback_target="${DEV_OIDC_LOOPBACK_TARGET:?DEV_OIDC_LOOPBACK_TARGET is required}"

current_pair() {
  [ -s "$certs/ca.pem" ] && [ -s "$certs/server.pem" ] && [ -s "$certs/server-key.pem" ] \
    && openssl x509 -in "$certs/ca.pem" -noout -ext nameConstraints 2>/dev/null \
      | grep -q 'DNS:localhost.pomerium.io' \
    && openssl x509 -in "$certs/server.pem" -noout -checkend 2592000 >/dev/null
}

if ! current_pair
then
  umask 077
  work=$(mktemp -d)
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -subj "/CN=Seizu local MCP gateway CA" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "nameConstraints=critical,permitted;DNS:localhost.pomerium.io,excluded;IP:0.0.0.0/0.0.0.0,excluded;IP:0:0:0:0:0:0:0:0/0:0:0:0:0:0:0:0" \
    -keyout "$work/ca-key.pem" -out "$work/ca.pem" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes \
    -subj "/CN=*.localhost.pomerium.io" \
    -keyout "$work/server-key.pem" -out "$work/server.csr" 2>/dev/null
  printf '%s\n' \
    "subjectAltName=DNS:*.localhost.pomerium.io" \
    "extendedKeyUsage=serverAuth" \
    "basicConstraints=CA:FALSE" > "$work/server.ext"
  openssl x509 -req -days 825 -in "$work/server.csr" \
    -CA "$work/ca.pem" -CAkey "$work/ca-key.pem" -set_serial "0x$(openssl rand -hex 16)" \
    -extfile "$work/server.ext" -out "$work/server.pem" 2>/dev/null
  chmod 644 "$work/ca.pem" "$work/server.pem"
  mv "$work/server-key.pem" "$work/server.pem" "$work/ca.pem" "$certs/"
  rm -rf "$work"
  # Left by the previous layout, which kept the CA key beside the certificate.
  rm -f "$certs/ca-key.pem" "$certs/ca.srl"
  echo "external-mcp-gateway: generated a new local CA at .compose/external-mcp-gateway/ca.pem;" \
    "restart Seizu to trust it"
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
