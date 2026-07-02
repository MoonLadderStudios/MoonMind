"""Hermetic route-contract coverage for the workflow console shell."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api_service.api.routers.workflow_console import _resolve_user_dependency_overrides
from api_service.db.base import get_async_session
from api_service.main import app

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
        },
        "entrypoints/dashboard.tsx": {
            "file": "assets/dashboard.js",
            "imports": [shared_key],
        },
    }
    (assets_dir / "dashboard.js").write_text(
        "console.log('dashboard');",
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

    mock_user = SimpleNamespace(id=uuid4(), email="workflow-routes@example.com")
    for dependency in _resolve_user_dependency_overrides():
        app.dependency_overrides[dependency] = lambda mock_user=mock_user: mock_user
    app.dependency_overrides[get_async_session] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(
        base_url="http://testserver",
        transport=transport,
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "path",
    (
        "/workflows",
        "/workflows/new",
        "/workflows/mm:workflow-123",
        (
            "/workflows/mm%3A5cd204e5-4f32-484a-a2ed-2222b214961c%3A"
            "%7B%7B.ScheduleTime%7D%7D-2026-06-27T13%3A00%3A00Z"
        ),
        "/workflows/mm:workflow-123/chat",
        "/workflows/mm:workflow-123/overview",
        "/workflows/mm:workflow-123/steps",
        "/workflows/mm:workflow-123/artifacts",
        "/workflows/mm:workflow-123/runs",
        "/workflows/mm:workflow-123/debug",
    ),
)
async def test_supported_workflow_routes_render_console_shell(
    async_client: AsyncClient,
    path: str,
) -> None:
    response = await async_client.get(path, follow_redirects=False)

    assert response.status_code == 200
    assert "moonmind-ui-boot" in response.text
    assert "/static/workflow_console/dist/assets/" in response.text

    boot_payload = _extract_boot_payload(response.text)
    assert boot_payload == {"page": "dashboard", "apiBase": "/api"}


@pytest.mark.parametrize(
    "path",
    (
        "/schedules",
        "/schedules/schedule-alpha",
        "/skills",
        "/skills/local/editor",
        "/settings",
        "/settings/provider-profiles",
        "/manifests",
        "/manifests/default-workflow",
        "/oauth-terminal",
        "/index-health",
    ),
)
async def test_supported_dashboard_deep_links_share_spa_shell(
    async_client: AsyncClient,
    path: str,
) -> None:
    response = await async_client.get(path, follow_redirects=False)

    assert response.status_code == 200
    assert "moonmind-ui-boot" in response.text
    assert "/static/workflow_console/dist/assets/" in response.text

    boot_payload = _extract_boot_payload(response.text)
    assert boot_payload == {"page": "dashboard", "apiBase": "/api"}


async def test_ui_info_endpoint_exposes_spa_capabilities_and_endpoints(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/api/ui/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["app"] == "moonmind"
    assert payload["apiBase"] == "/api"
    assert payload["features"]["workflowList"] is True
    assert payload["features"]["settings"] is True
    assert payload["endpoints"]["workflows"] == "/api/executions"
    assert payload["endpoints"]["workflowDetail"] == "/api/executions/{workflowId}"
    assert "dashboardConfig" in payload


@pytest.mark.parametrize(
    "path",
    (
        "/tasks/list",
        "/tasks/new",
        "/tasks/queue/new",
        "/tasks/mm:workflow-123",
    ),
)
async def test_removed_task_routes_do_not_redirect_or_render_console(
    async_client: AsyncClient,
    path: str,
) -> None:
    response = await async_client.get(path, follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers
    assert "moonmind-ui-boot" not in response.text
    assert "/static/workflow_console/dist/assets/" not in response.text
