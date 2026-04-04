import json

import pytest

from api_service.ui_assets import (
    InvalidDevServerUrlError,
    ViteAssetResolver,
    ui_assets,
)
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


def test_ui_assets_uses_vite_dev_server_when_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MOONMIND_UI_DEV_SERVER_URL", "http://127.0.0.1:5173/")
    monkeypatch.setenv("VITE_MANIFEST_PATH", "non-existent-manifest.json")

    html = ui_assets("mission-control")

    assert 'src="http://127.0.0.1:5173/@vite/client"' in html
    assert 'src="http://127.0.0.1:5173/entrypoints/mission-control.tsx"' in html
    assert "/static/task_dashboard/dist/" not in html


def test_ui_assets_rejects_invalid_vite_dev_server_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_UI_DEV_SERVER_URL", "vite.local:5173")

    with pytest.raises(InvalidDevServerUrlError):
        ui_assets("tasks-list")


def test_generate_boot_payload():
    payload = generate_boot_payload(page="test_page")
    parsed = json.loads(payload)
    assert parsed["page"] == "test_page"
    assert parsed["apiBase"] == "/api"
    assert "features" not in parsed

    payload2 = generate_boot_payload(page="test_page", features={"oauth": True})
    parsed2 = json.loads(payload2)
    assert parsed2["features"] == {"oauth": True}
