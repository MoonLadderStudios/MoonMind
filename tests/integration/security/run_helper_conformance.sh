#!/bin/sh
set -eu

proxy=http://egress-proxy:3128

# A managed helper receives the same single restricted attachment and proxy
# contract as its parent workload; it cannot reach the origin or gateway
# directly and has no Docker authority to attach another network.
curl --proxy "$proxy" --insecure --fail --silent --max-time 5 \
  https://allowed.test/health | grep -q approved
if HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= NO_PROXY=allowed.test \
  curl --insecure --fail --silent --max-time 4 \
  https://allowed.test/health >/dev/null 2>&1; then
  echo "managed helper bypassed the proxy" >&2
  exit 1
fi
if curl --insecure --fail --silent --max-time 4 \
  https://198.19.0.1/ >/dev/null 2>&1; then
  echo "managed helper reached the bridge gateway" >&2
  exit 1
fi
if test -S /var/run/docker.sock; then
  echo "managed helper unexpectedly has Docker authority" >&2
  exit 1
fi
