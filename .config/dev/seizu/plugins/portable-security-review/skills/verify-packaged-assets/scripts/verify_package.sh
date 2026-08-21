#!/bin/sh
set -eu

token=$(sed -n 's/^verification-token: //p' references/live-verification.md)
message=${1:-}
printf 'verification_token=%s\nmessage=%s\n' "$token" "$message"
