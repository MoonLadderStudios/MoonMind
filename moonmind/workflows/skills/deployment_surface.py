"""Read-only qualification of the release's actual HTTP surface."""

from __future__ import annotations

import urllib.request
from urllib.parse import urljoin, urlsplit


def verify_surface(base_url):
    from tools.verify_deployed_ui_assets import verify_deployed_ui_assets

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
    with urllib.request.urlopen(urljoin(base_url, "/healthz"), timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("Deployment health endpoint is unavailable")
    errors = verify_deployed_ui_assets(base_url)
    if errors:
        raise RuntimeError(
            "Deployment dashboard/API verification failed: " + "; ".join(errors[:4])
        )
    return {
        "baseUrl": base_url,
        "status": "verified",
        "checks": ["healthz", "dashboard", "assets", "api/ui/info"],
    }


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(verify_surface(sys.argv[1])))
