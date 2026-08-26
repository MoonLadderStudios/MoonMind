#!/usr/bin/env python3
"""Verify a running MoonMind deployment serves a coherent dashboard asset set.

The dashboard SPA is code-split: the served HTML references a hashed entry
bundle, and the entry bundle references hashed lazy page chunks. Every one of
those files must come from the same build. A partially patched static
directory (for example a ``docker cp`` hot-patch that copies the entry bundle
but not its chunks, or a stale volume mounted over the image's dist) serves a
mix of builds: the page appears to work for browsers with cached chunks while
fresh clients get 404s and silently degraded navigation.

This check fetches the deployed HTML, the assets it references, and every
lazy chunk referenced by the entry bundle, and fails on the first
incoherence. Run it after any deploy or hot-patch:

    python tools/verify_deployed_ui_assets.py --base-url http://localhost:7000
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urljoin

DEFAULT_PAGE_PATH = "/workflows"
UI_INFO_PATH = "/api/ui/info"

# <script src="..."> and <link rel="stylesheet" href="..."> asset references.
_SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=\"([^\"]+)\"", re.IGNORECASE)
_STYLESHEET_RE = re.compile(
    r"<link[^>]+rel=\"stylesheet\"[^>]+href=\"([^\"]+)\"|<link[^>]+href=\"([^\"]+)\"[^>]+rel=\"stylesheet\"",
    re.IGNORECASE,
)
# Hashed sibling-chunk references inside a built entry bundle.
_CHUNK_REF_RE = re.compile(r"assets/[A-Za-z0-9_.-]+\.(?:js|css)")

Fetcher = Callable[[str], tuple[int, bytes]]


def _default_fetcher(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "moonmind-ui-asset-check"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""
    except OSError:
        return 0, b""


def extract_page_assets(html: str) -> list[str]:
    """Return script/stylesheet asset URLs referenced by a served page."""
    assets = list(_SCRIPT_SRC_RE.findall(html))
    for match in _STYLESHEET_RE.findall(html):
        assets.extend(part for part in match if part)
    return [asset for asset in dict.fromkeys(assets) if "/assets/" in asset or asset.endswith((".js", ".css"))]

def extract_chunk_refs(entry_js: str) -> list[str]:
    """Return hashed chunk paths referenced inside a built entry bundle."""
    return list(dict.fromkeys(_CHUNK_REF_RE.findall(entry_js)))


def verify_deployed_ui_assets(
    base_url: str,
    page_path: str = DEFAULT_PAGE_PATH,
    fetch: Fetcher = _default_fetcher,
) -> list[str]:
    """Return a list of coherence errors for the deployment at ``base_url``."""
    errors: list[str] = []
    page_url = urljoin(base_url, page_path)
    status, body = fetch(page_url)
    if status != 200:
        return [f"Page {page_url} returned HTTP {status}"]
    html = body.decode("utf-8", errors="replace")

    page_assets = extract_page_assets(html)
    if not page_assets:
        return [f"Page {page_url} references no script/stylesheet assets; cannot verify"]

    chunk_refs: list[str] = []
    asset_dirs: list[str] = []
    for asset in page_assets:
        asset_url = urljoin(page_url, asset)
        asset_status, asset_body = fetch(asset_url)
        if asset_status != 200:
            errors.append(f"Referenced asset missing (HTTP {asset_status}): {asset_url}")
            continue
        if asset.endswith(".js"):
            asset_dirs.append(asset_url.rsplit("/assets/", 1)[0] + "/" if "/assets/" in asset_url else asset_url)
            chunk_refs.extend(extract_chunk_refs(asset_body.decode("utf-8", errors="replace")))

    seen: set[str] = set()
    for chunk in chunk_refs:
        last_url: str | None = None
        last_status: int | None = None
        for asset_dir in asset_dirs or [page_url]:
            chunk_url = urljoin(asset_dir, chunk)
            if chunk_url in seen:
                break
            chunk_status, _ = fetch(chunk_url)
            if chunk_status == 200:
                seen.add(chunk_url)
                break
            last_url = chunk_url
            last_status = chunk_status
        else:
            errors.append(
                f"Entry bundle references a missing chunk (HTTP {last_status}): {last_url}"
            )

    ui_info_url = urljoin(base_url, UI_INFO_PATH)
    info_status, info_body = fetch(ui_info_url)
    if info_status != 200:
        errors.append(f"{ui_info_url} returned HTTP {info_status}; cannot confirm server build id")
    else:
        try:
            build_id = json.loads(info_body.decode("utf-8", errors="replace")).get("buildId")
        except (ValueError, AttributeError):
            build_id = None
        print(f"Server build id: {build_id or '(unset)'}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:7000", help="Deployment root URL")
    parser.add_argument("--page", default=DEFAULT_PAGE_PATH, help="SPA page path to verify")
    args = parser.parse_args()

    errors = verify_deployed_ui_assets(args.base_url, args.page)
    if errors:
        print("Deployed UI asset verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(
            "The static asset set is incoherent (mixed builds). Redeploy the image cleanly; "
            "do not hot-patch individual files into the running container.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {args.base_url} serves a coherent dashboard asset set for {args.page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
