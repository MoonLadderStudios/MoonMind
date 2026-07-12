"""Unit tests for the deployed dashboard asset coherence check."""
from __future__ import annotations

import importlib.util
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "tools" / "verify_deployed_ui_assets.py"

_spec = importlib.util.spec_from_file_location("verify_deployed_ui_assets", MODULE_PATH)
assert _spec and _spec.loader
verify_deployed_ui_assets = importlib.util.module_from_spec(_spec)
sys.modules["verify_deployed_ui_assets"] = verify_deployed_ui_assets
_spec.loader.exec_module(verify_deployed_ui_assets)

PAGE_HTML = """
<!doctype html>
<html>
<head>
  <script type="module" crossorigin src="/static/workflow_console/dist/assets/dashboard-AAA.js"></script>
  <link rel="stylesheet" crossorigin href="/static/workflow_console/dist/assets/dashboard-BBB.css">
</head>
<body><div id="dashboard-app-root"></div></body>
</html>
"""

ENTRY_JS = 'import("./workflow-list-CCC.js");const x="assets/workflow-list-CCC.js";const y="assets/skills-DDD.js";'

def _fetcher(responses: dict[str, tuple[int, bytes]]):
    def fetch(url: str) -> tuple[int, bytes]:
        return responses.get(url, (404, b""))

    return fetch

BASE = "http://deploy.example:7000"
ASSET_BASE = f"{BASE}/static/workflow_console/dist/assets"

def _coherent_responses() -> dict[str, tuple[int, bytes]]:
    return {
        f"{BASE}/workflows": (200, PAGE_HTML.encode()),
        f"{ASSET_BASE}/dashboard-AAA.js": (200, ENTRY_JS.encode()),
        f"{ASSET_BASE}/dashboard-BBB.css": (200, b"body{}"),
        f"{ASSET_BASE}/workflow-list-CCC.js": (200, b"//chunk"),
        f"{ASSET_BASE}/skills-DDD.js": (200, b"//chunk"),
        f"{BASE}/api/ui/info": (200, b'{"buildId": "20260711.1"}'),
    }

def test_extracts_page_assets_and_chunk_refs() -> None:
    assets = verify_deployed_ui_assets.extract_page_assets(PAGE_HTML)
    assert assets == [
        "/static/workflow_console/dist/assets/dashboard-AAA.js",
        "/static/workflow_console/dist/assets/dashboard-BBB.css",
    ]
    chunks = verify_deployed_ui_assets.extract_chunk_refs(ENTRY_JS)
    assert chunks == ["assets/workflow-list-CCC.js", "assets/skills-DDD.js"]

def test_coherent_deployment_passes() -> None:
    errors = verify_deployed_ui_assets.verify_deployed_ui_assets(
        BASE, fetch=_fetcher(_coherent_responses())
    )
    assert errors == []

def test_missing_lazy_chunk_fails() -> None:
    # The exact production incident: the entry bundle and CSS were hot-patched
    # into the running container, but the lazy page chunks they reference were
    # not, so fresh clients 404 on navigation while cached browsers render a
    # Franken-UI mixing builds.
    responses = _coherent_responses()
    del responses[f"{ASSET_BASE}/workflow-list-CCC.js"]
    errors = verify_deployed_ui_assets.verify_deployed_ui_assets(
        BASE, fetch=_fetcher(responses)
    )
    assert any("workflow-list-CCC.js" in error and "404" in error for error in errors)

def test_missing_entry_asset_fails() -> None:
    responses = _coherent_responses()
    del responses[f"{ASSET_BASE}/dashboard-BBB.css"]
    errors = verify_deployed_ui_assets.verify_deployed_ui_assets(
        BASE, fetch=_fetcher(responses)
    )
    assert any("dashboard-BBB.css" in error for error in errors)

def test_unreachable_page_fails_fast() -> None:
    errors = verify_deployed_ui_assets.verify_deployed_ui_assets(
        BASE, fetch=_fetcher({})
    )
    assert errors == [f"Page {BASE}/workflows returned HTTP 404"]


def test_multiple_asset_dirs_resolves_chunk_from_secondary_directory() -> None:
    responses = _coherent_responses()
    responses[f"{BASE}/workflows"] = (
        200,
        PAGE_HTML.replace(
            "</head>",
            '<script type="module" src="/static/other_console/dist/assets/other-AAA.js"></script></head>',
        ).encode(),
    )
    responses[f"{BASE}/static/other_console/dist/assets/other-AAA.js"] = (
        200,
        b'const chunk="assets/other-chunk.js";',
    )
    responses[f"{BASE}/static/other_console/dist/assets/other-chunk.js"] = (
        200,
        b"// other chunk",
    )

    errors = verify_deployed_ui_assets.verify_deployed_ui_assets(
        BASE, fetch=_fetcher(responses)
    )

    assert errors == []


def test_default_fetcher_maps_network_errors_to_zero_status() -> None:
    with patch(
        "verify_deployed_ui_assets.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        assert verify_deployed_ui_assets._default_fetcher(BASE) == (0, b"")
