"""Integration coverage for the OAuth terminal Mission Control shell."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api_service.main import app
from api_service.api.routers.task_dashboard import _resolve_user_dependency_overrides

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

class _BootPayloadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capturing = False
        self.payload_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attr_map = dict(attrs)
        self._capturing = (
            attr_map.get("id") == "moonmind-ui-boot"
            and attr_map.get("type") == "application/json"
        )

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self.payload_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._capturing = False

def _extract_boot_payload(response_text: str) -> dict[str, object]:
    parser = _BootPayloadParser()
    parser.feed(response_text)
    assert parser.payload_parts
    return json.loads("".join(parser.payload_parts))

def _write_dashboard_test_manifest(root: Path) -> Path:
    dist_root = root / "dist"
    manifest_dir = dist_root / ".vite"
    assets_dir = dist_root / "assets"
    manifest_dir.mkdir(parents=True)
    assets_dir.mkdir()

    shared_key = "_mountPage-shared.js"
    manifest: dict[str, dict[str, object]] = {
        shared_key: {
            "file": "assets/mountPage.js",
            "css": ["assets/mountPage.css"],
        }
    }
    manifest["entrypoints/mission-control.tsx"] = {
        "file": "assets/mission-control.js",
        "imports": [shared_key],
    }
    (assets_dir / "mission-control.js").write_text(
        "console.log('mission-control');",
        encoding="utf-8",
    )
    (assets_dir / "mountPage.js").write_text("console.log('shared');", encoding="utf-8")
    (assets_dir / "mountPage.css").write_text("body { color: red; }", encoding="utf-8")

    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path

@pytest_asyncio.fixture
async def async_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    monkeypatch.delenv("MOONMIND_UI_DEV_SERVER_URL", raising=False)
    monkeypatch.setenv(
        "VITE_MANIFEST_PATH", str(_write_dashboard_test_manifest(tmp_path))
    )

    mock_user = SimpleNamespace(id=uuid4(), email="integration@example.com")
    for dependency in _resolve_user_dependency_overrides():
        app.dependency_overrides[dependency] = lambda mock_user=mock_user: mock_user

    transport = ASGITransport(app=app)
    async with AsyncClient(
        base_url="http://testserver",
        transport=transport,
    ) as client:
        yield client

    app.dependency_overrides.clear()

async def test_oauth_terminal_shell_route_renders_boot_payload(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/oauth-terminal?session_id=oas_integration_shell")

    assert response.status_code == 200
    assert "moonmind-ui-boot" in response.text

    boot_payload = _extract_boot_payload(response.text)
    assert boot_payload["page"] == "oauth-terminal"
    assert boot_payload["initialData"]["sessionId"] == "oas_integration_shell"
    assert boot_payload["initialData"]["layout"]["dataWidePanel"] is True
    assert (
        boot_payload["initialData"]["dashboardConfig"]["initialPath"]
        == "/oauth-terminal"
    )
