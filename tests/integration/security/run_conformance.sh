#!/bin/sh
set -eu

proxy=http://egress-proxy:3128

retry=0
until curl --proxy "$proxy" --insecure --fail --silent --max-time 5 \
  https://allowed.test/health | grep -q approved; do
  retry=$((retry + 1))
  test "$retry" -lt 20
  sleep 1
done

expect_denied() {
  if curl --proxy "$proxy" --location --insecure --fail --silent --max-time 4 "$1" >/dev/null 2>&1; then
    echo "unexpectedly allowed: $1" >&2
    exit 1
  fi
}

# Destination, DNS-name, local/control-plane, direct-IP, IPv6, and mapped-IPv6
# attempts are rejected by the real proxy rather than cooperating application
# code. An unapproved redirect is re-evaluated and denied on the second request.
expect_denied https://forbidden.test/
expect_denied https://93.184.216.34/
expect_denied https://127.0.0.1/
expect_denied https://169.254.169.254/
expect_denied https://10.0.0.1/
expect_denied https://[::1]/
expect_denied https://[::ffff:127.0.0.1]/
expect_denied https://allowed.test:444/
expect_denied https://allowed.test/redirect-forbidden

# CONNECT is the sole accepted method and only to TLS port 443.
if curl --proxy "$proxy" --request TRACE --fail --silent --max-time 4 \
  http://allowed.test/ >/dev/null 2>&1; then
  echo "alternate proxy method unexpectedly allowed" >&2
  exit 1
fi

# Clearing proxy variables proves the workload has no direct route to the
# dual-homed origin, so NO_PROXY/application cooperation cannot bypass policy.
if HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= NO_PROXY=allowed.test \
  curl --insecure --fail --silent --max-time 4 \
  https://allowed.test/health >/dev/null 2>&1; then
  echo "direct proxy bypass unexpectedly allowed" >&2
  exit 1
fi
