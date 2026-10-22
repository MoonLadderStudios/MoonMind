"""Read-only verification through the deployment's actual operator origin."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_operator_url(url: str, *, timeout_seconds: int = 30) -> str:
    """Require the protected settings route, preserving TLS and ingress auth.

    A login redirect, public health, and HTML login page cannot prove access.
    No session or trusted identity assertion is manufactured for the probe.
    """
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment):
        raise RuntimeError("operator origin must be an HTTP(S) URL without credentials or query")
    request = urllib.request.Request(
        url.rstrip("/") + "/api/v1/settings/catalog", method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(
            request, timeout=timeout_seconds
        ) as response:
            if response.getcode() != 200 or response.headers.get_content_type() != "application/json":
                raise RuntimeError("protected operator route did not return HTTP 200 JSON")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"protected operator route returned HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"protected operator route could not be reached: {exc}") from exc
    return "ok"


if __name__ == "__main__":
    try:
        print(check_operator_url(sys.argv[1]))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
