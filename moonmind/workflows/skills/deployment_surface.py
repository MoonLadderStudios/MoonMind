"""Read-only qualification of the release's actual HTTP surface."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import sys
from contextlib import redirect_stdout
from functools import partial
from urllib.parse import urljoin, urlsplit, urlunsplit


def validate_operator_url(base_url):
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Deployment surface requires an HTTP origin without credentials"
        )
    return base_url


def operator_urls(configuration):
    """Resolve actual operator origins from preserved deployment authority."""
    api = configuration.get("services", {}).get("api", {})
    declared = str(
        api.get("environment", {}).get("MOONMIND_PUBLIC_BASE_URL") or ""
    ).strip()
    if declared:
        return [validate_operator_url(declared)]
    urls = []
    for binding in api.get("ports", []):
        if (
            binding.get("protocol", "tcp") != "tcp"
            or int(binding.get("target", 0)) != 8000
        ):
            continue
        host = binding.get("host_ip") or "0.0.0.0"
        address = ipaddress.ip_address(host)
        if address.is_unspecified:
            raise ValueError(
                "Wildcard API bindings require the existing operator URL in MOONMIND_PUBLIC_BASE_URL before release"
            )
        port = str(binding.get("published") or "")
        if not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ValueError(
                "Operator verification requires a fixed published API port"
            )
        authority = f"[{address}]" if address.version == 6 else str(address)
        urls.append(f"http://{authority}:{port}")
    if not urls:
        raise ValueError(
            "Operator verification requires a published API binding or MOONMIND_PUBLIC_BASE_URL"
        )
    return sorted(set(urls))


def validate_headers(headers):
    if not isinstance(headers, dict) or len(headers) > 2:
        raise ValueError(
            "Operator credentials must contain only Cookie or Authorization"
        )
    seen = set()
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or name.lower() not in {"authorization", "cookie"}
            or name.lower() in seen
            or not isinstance(value, str)
            or len(value) > 16384
            or any(ord(character) < 32 or ord(character) > 126 for character in value)
        ):
            raise ValueError(
                "Operator credentials contain an invalid authentication header"
            )
        seen.add(name.lower())
    return headers


def _origin(url):
    parsed = urlsplit(url)
    return (
        parsed.scheme,
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def fetch_surface(
    url, *, docker_host_gateway=False, headers=None, credential_origin=None
):
    """Preserve HTTP/TLS authority when crossing a Docker Desktop host gateway."""
    parsed = urlsplit(url)
    validate_operator_url(urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
    host = parsed.hostname
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    endpoint = "host.docker.internal" if docker_host_gateway and loopback else host
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection = http.client.HTTPConnection(host, port, timeout=30)
    transport = socket.create_connection((endpoint, port), timeout=30)
    try:
        if parsed.scheme == "https":
            # Certificate verification and SNI retain the original operator host.
            transport = ssl.create_default_context().wrap_socket(
                transport, server_hostname=host
            )
        connection.sock = transport
        authorized_headers = (
            headers
            if credential_origin is not None
            and _origin(url) == _origin(credential_origin)
            else {}
        )
        connection.request(
            "GET",
            (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
            headers={
                "Host": parsed.netloc,
                "User-Agent": "moonmind-ui-asset-check",
                **(authorized_headers or {}),
            },
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()
        transport.close()


def verify_surface(
    base_url, *, docker_host_gateway=False, headers=None, expected_release=None
):
    from tools.verify_deployed_ui_assets import verify_deployed_ui_assets

    validate_operator_url(base_url)
    fetch = partial(
        fetch_surface,
        docker_host_gateway=docker_host_gateway,
        headers=validate_headers(headers or {}),
        credential_origin=base_url,
    )
    status, health = fetch(urljoin(base_url, "/healthz"))
    if status != 200:
        raise RuntimeError("Deployment health endpoint is unavailable")
    if expected_release is not None:
        identity = json.loads(health).get("workerCodeFreshness", {}).get("api", {})
        if (
            identity.get("status") != "healthy"
            or identity.get("startupDigest") != expected_release
            or identity.get("currentDigest") != expected_release
        ):
            raise RuntimeError(
                "Operator origin does not serve the selected immutable release"
            )
    # The portable asset checker emits diagnostics; stdout is the probe receipt.
    with redirect_stdout(sys.stderr):
        errors = verify_deployed_ui_assets(base_url, fetch=fetch)
    if errors:
        raise RuntimeError(
            "Deployment dashboard/API verification failed; HTTP 401/403 requires an existing authorized credential in the deployment-owned operator-http-headers.json: "
            + "; ".join(errors[:4])
        )
    return {
        "baseUrl": base_url,
        "status": "verified",
        "checks": ["healthz", "dashboard", "assets", "api/ui/info"],
        "transport": "docker-host-gateway" if docker_host_gateway else "host-network",
        "releaseDigest": expected_release,
    }


if __name__ == "__main__":
    import argparse
    import json
    import signal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--docker-host-gateway", action="store_true")
    parser.add_argument("--expected-release")
    args = parser.parse_args()
    # Bound the ephemeral probe even if a server trickles bytes across timeouts.
    signal.alarm(120)
    credentials = sys.stdin.buffer.read(65537)
    if len(credentials) > 65536:
        raise ValueError("Operator credential input exceeds its bound")
    print(
        json.dumps(
            verify_surface(
                args.base_url,
                docker_host_gateway=args.docker_host_gateway,
                headers=json.loads(credentials) if credentials else {},
                expected_release=args.expected_release,
            )
        )
    )
