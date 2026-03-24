import pytest
import json
import os
from api_service.ui_assets import ViteAssetResolver, ui_assets
from api_service.ui_boot import generate_boot_payload

def test_vite_asset_resolver_missing_file():
    resolver = ViteAssetResolver("non-existent-manifest.json")
    assert resolver.get_manifest() == {}
    assert resolver.resolve_entrypoint("unknown") == {}

def test_vite_asset_resolver_valid_file(tmp_path):
    manifest_data = {
        "entrypoints/test.tsx": {
            "file": "assets/test.js",
            "css": ["assets/test.css"]
        }
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data))

    resolver = ViteAssetResolver(str(manifest_file))
    entry = resolver.resolve_entrypoint("test")

    assert entry["file"] == "assets/test.js"
    assert entry["css"] == ["assets/test.css"]

def test_generate_boot_payload():
    payload = generate_boot_payload(page="test_page")
    parsed = json.loads(payload)
    assert parsed["page"] == "test_page"
    assert parsed["apiBase"] == "/api"
    assert "features" not in parsed

    payload2 = generate_boot_payload(page="test_page", features={"oauth": True})
    parsed2 = json.loads(payload2)
    assert parsed2["features"] == {"oauth": True}