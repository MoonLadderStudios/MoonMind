"""Unit tests for workflow console shell routes."""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from io import BytesIO
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.workflow_console import (
    _get_temporal_service,
    _is_allowed_path,
    _resolve_user_dependency_overrides,
    router,
)

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

@contextmanager
def _client_with_mock_service(
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router)

    mock_user = SimpleNamespace(id=uuid4(), email="dashboard@example.com")
    mock_service = AsyncMock()
    mock_service.list_jobs_page.return_value = SimpleNamespace(
        items=tuple(),
        page_size=50,
        next_cursor=None,
    )
    for dependency in _resolve_user_dependency_overrides():
        app.dependency_overrides[dependency] = lambda mock_user=mock_user: mock_user
    app.dependency_overrides[_get_temporal_service] = lambda: mock_service

    original_manifest = os.environ.get("VITE_MANIFEST_PATH")
    tmpdir: tempfile.TemporaryDirectory[str] | None = None
    if (
        "MOONMIND_UI_DEV_SERVER_URL" not in os.environ
        and "VITE_MANIFEST_PATH" not in os.environ
    ):
        tmpdir = tempfile.TemporaryDirectory()
        if monkeypatch is not None:
            monkeypatch.setenv("VITE_MANIFEST_PATH", str(
                _write_dashboard_test_manifest(Path(tmpdir.name))
            ))
        else:
            os.environ["VITE_MANIFEST_PATH"] = str(
                _write_dashboard_test_manifest(Path(tmpdir.name))
            )

    try:
        with TestClient(app) as test_client:
            yield test_client, mock_service
    finally:
        if monkeypatch is None:
            if original_manifest is None:
                os.environ.pop("VITE_MANIFEST_PATH", None)
            else:
                os.environ["VITE_MANIFEST_PATH"] = original_manifest
        if tmpdir is not None:
            tmpdir.cleanup()

    app.dependency_overrides.clear()

@pytest.fixture
def client() -> Iterator[TestClient]:
    with _client_with_mock_service() as (test_client, _mock_service):
        yield test_client

def test_workflow_console_api_routes_are_workflow_native() -> None:
    route_paths = {getattr(route, "path", "") for route in router.routes}

    assert not any(path == "/tasks" or path.startswith("/tasks/") for path in route_paths)
    assert "/workflows" in route_paths
    assert "/workflows/new" in route_paths
    assert "/workflows/{workflow_path:path}" in route_paths
    assert "/api/workflows/skills" in route_paths
    assert "/api/workflows/skills/upload" in route_paths

def test_allowed_path_helper_accepts_known_routes() -> None:
    assert not _is_allowed_path("system")
    assert not _is_allowed_path("queue")
    assert not _is_allowed_path("queue/new")
    assert not _is_allowed_path("queue/123")
    assert _is_allowed_path("mm:123")
    assert _is_allowed_path("123e4567-e89b-12d3-a456-426614174000")
    assert _is_allowed_path("mm:123e4567-e89b-12d3-a456-426614174000")
    assert _is_allowed_path("mm:01JNX7SYH6A3K1V8Q2D7E9F4AB")
    assert _is_allowed_path("mm:01JNX7SYH6A3K1V8Q2D7E9F4AB/steps")
    assert _is_allowed_path("mm:01JNX7SYH6A3K1V8Q2D7E9F4AB/artifacts")
    assert _is_allowed_path("mm:01JNX7SYH6A3K1V8Q2D7E9F4AB/runs")
    assert not _is_allowed_path("new")
    assert not _is_allowed_path("new/steps")
    assert not _is_allowed_path("manifests")
    assert not _is_allowed_path("manifests/new")
    assert not _is_allowed_path("manifests/steps")
    assert not _is_allowed_path("schedules")
    assert not _is_allowed_path("settings")
    assert not _is_allowed_path("workers")
    assert not _is_allowed_path("secrets")
    assert not _is_allowed_path("temporal/runs")

def test_allowed_path_helper_rejects_unknown_routes() -> None:
    assert not _is_allowed_path("")
    assert not _is_allowed_path("queue/new/extra")
    assert not _is_allowed_path("queue//")
    assert not _is_allowed_path("queue/<script>alert(1)</script>")
    assert not _is_allowed_path("queue/not allowed")

def test_root_route_renders_dashboard_shell(client: TestClient) -> None:
    response = client.get("/workflows", follow_redirects=False)

    assert response.status_code == 200
    assert "moonmind-ui-boot" in response.text
    boot_payload = _extract_boot_payload(response.text)
    assert boot_payload["page"] == "workflow-list"
    assert boot_payload["initialData"]["dashboardConfig"]["initialPath"] == "/workflows"

def test_static_sub_routes_render_react_shell(client: TestClient) -> None:
    for path in (
        "/workflows/new",
        "/skills",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "moonmind-ui-boot" in response.text
        assert 'type="module"' in response.text
        assert "/static/workflow_console/dist/assets/" in response.text
        assert 'class="dashboard-shell-full"' in response.text
        assert 'class="masthead-brand"' in response.text
        assert 'src="/static/workflow_console/moonmindlogo.webp"' in response.text
        assert "MOONMIND OPERATIONS" not in response.text
        assert "workflow-console-config" not in response.text
        assert "/static/workflow_console/dashboard.js" not in response.text
        assert 'id="mission-control-root"' in response.text
        assert 'id="dashboard-alerts-root"' not in response.text
        assert "marked.min.js" not in response.text
        assert "__moonmind_customElementsDefineGuard" not in response.text

    for path in (
        "/workflows",
        "/manifests",
        "/schedules",
        "/settings",
        "/proposals",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "moonmind-ui-boot" in response.text
        assert 'type="module"' in response.text
        assert "/static/workflow_console/dist/assets/" in response.text
        assert 'id="mission-control-root"' in response.text
        assert 'id="dashboard-alerts-root"' not in response.text
        assert "marked.min.js" not in response.text
        assert "__moonmind_customElementsDefineGuard" not in response.text

def test_mission_control_logo_asset_exists() -> None:
    asset_path = Path("api_service/static/workflow_console/moonmindlogo.webp")

    assert asset_path.read_bytes().startswith(b"RIFF")
    assert asset_path.stat().st_size < 25_000

def test_task_create_route_uses_canonical_boot_payload(client: TestClient) -> None:
    """GET /workflows/new renders the workflow-start shell with server runtime config."""
    response = client.get("/workflows/new")

    assert response.status_code == 200
    assert "moonmind-ui-boot" in response.text
    boot_payload = _extract_boot_payload(response.text)

    assert boot_payload["page"] == "workflow-start"
    dashboard_config = boot_payload["initialData"]["dashboardConfig"]
    assert dashboard_config["initialPath"] == "/workflows/new"
    assert dashboard_config["sources"]["temporal"]["create"].startswith("/api/")
    assert dashboard_config["sources"]["temporal"]["artifactCreate"].startswith(
        "/api/"
    )

def test_oauth_terminal_route_uses_terminal_boot_payload(client: TestClient) -> None:
    response = client.get("/oauth-terminal?session_id=oas_route_shell")

    assert response.status_code == 200
    assert "moonmind-ui-boot" in response.text
    boot_payload = _extract_boot_payload(response.text)
    assert boot_payload["page"] == "oauth-terminal"
    assert boot_payload["initialData"]["sessionId"] == "oas_route_shell"
    assert boot_payload["initialData"]["dashboardConfig"]["initialPath"] == "/oauth-terminal"
    assert boot_payload["initialData"]["layout"]["dataWidePanel"] is True

def test_removed_task_routes_do_not_redirect_or_render_console(client: TestClient) -> None:
    for path in (
        "/tasks/list",
        "/tasks/new",
        "/tasks/queue/new",
        f"/tasks/{uuid4()}",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404
        assert "moonmind-ui-boot" not in response.text
        assert "location" not in response.headers

def test_legacy_manifest_submit_route_redirects_to_unified_manifests_page(
    client: TestClient,
) -> None:
    response = client.get("/manifests/new", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/manifests"

def test_legacy_manifest_submit_route_openapi_documents_redirect(
    client: TestClient,
) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    route = response.json()["paths"]["/manifests/new"]["get"]
    assert "307" in route["responses"]
    assert "200" not in route["responses"]
    assert "application/json" not in route["responses"]["307"].get("content", {})

def test_navigation_exposes_single_manifest_destination(client: TestClient) -> None:
    response = client.get("/manifests")

    assert response.status_code == 200
    assert 'href="/manifests"' in response.text
    assert "Manifest Submit" not in response.text
    assert 'href="/manifests/new"' not in response.text

def test_react_shell_wraps_navigation_in_centered_masthead_slot(
    client: TestClient,
) -> None:
    response = client.get("/workflows")

    assert response.status_code == 200
    assert '<div class="masthead-nav">' in response.text
    assert response.text.index('<div class="masthead-nav">') < response.text.index(
        'id="mission-control-nav"'
    )

    title_meta_marker = '<div class="masthead-title-meta">'
    if title_meta_marker in response.text:
        assert response.text.index('id="mission-control-nav"') < response.text.index(
            title_meta_marker
        )

def test_trailing_slash_alias_routes_return_404_not_detail_page(client: TestClient) -> None:
    """Trailing-slash variants /workflows/new/ and /workflows/ must not render a detail shell."""
    for path in ("/workflows/new/", "/workflows/"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "dashboard_route_not_found"

def test_react_shell_uses_vite_dev_server_assets_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_UI_DEV_SERVER_URL", "http://127.0.0.1:5173")

    with _client_with_mock_service() as (client, _mock_service):
        response = client.get("/workflows")

    assert response.status_code == 200
    assert response.text.count('src="http://127.0.0.1:5173/@vite/client"') == 1
    assert 'src="http://127.0.0.1:5173/entrypoints/mission-control.tsx"' in response.text
    assert "/static/workflow_console/dist/assets/" not in response.text

def test_detail_sub_routes_render_dashboard_shell(client: TestClient) -> None:
    for path in (
        f"/workflows/{uuid4()}",
        f"/workflows/mm:{uuid4()}",
        "/workflows/mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
        "/workflows/mm:workflow-123",
        "/workflows/mm:workflow-123/steps",
        "/workflows/mm:workflow-123/artifacts",
        "/workflows/mm:workflow-123/runs",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "moonmind-ui-boot" in response.text
        assert 'type="module"' in response.text
        assert "/static/workflow_console/dist/assets/" in response.text

def test_data_wide_panel_on_selected_react_routes(client: TestClient) -> None:
    for path in ("/workflows", "/proposals"):
        response = client.get(path)
        assert response.status_code == 200
        assert '"dataWidePanel":true' in response.text
    for path in ("/manifests", "/settings"):
        response = client.get(path)
        assert response.status_code == 200
        assert '"dataWidePanel":false' in response.text

def test_top_level_detail_deep_links_render_react_shell(client: TestClient) -> None:
    for path, entrypoint in (
        ("/proposals/123e4567-e89b-12d3-a456-426614174000", "proposals"),
        ("/manifests/nightly-docs", "manifests"),
        ("/schedules/123e4567-e89b-12d3-a456-426614174000", "schedules"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "moonmind-ui-boot" in response.text
        payload = _extract_boot_payload(response.text)
        assert payload["page"] == entrypoint

def test_legacy_settings_subroutes_redirect_to_unified_settings(client: TestClient) -> None:
    workers = client.get("/workers", follow_redirects=False)
    assert workers.status_code == 307
    assert workers.headers["location"] == "/settings?section=operations"

    secrets = client.get("/secrets", follow_redirects=False)
    assert secrets.status_code == 307
    assert secrets.headers["location"] == "/settings?section=providers-secrets"

def test_react_tasks_list_and_detail_boot_include_dashboard_config(client: TestClient) -> None:
    response = client.get("/workflows")
    assert response.status_code == 200
    assert "dashboardConfig" in response.text
    detail = client.get(f"/workflows/{uuid4()}")
    assert detail.status_code == 200
    assert "dashboardConfig" in detail.text

def test_react_shell_renders_build_metadata_with_accurate_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_BUILD_ID", "20260408.1703")

    with _client_with_mock_service(monkeypatch) as (client, _mock_service):
        response = client.get("/workflows")

    assert response.status_code == 200
    assert 'title="MoonMind image version"' in response.text
    assert '<span class="version-badge-value">v20260408.1703</span>' in response.text
    assert "MoonMind</span>" not in response.text
    assert 'title="Codex CLI version"' not in response.text

def test_react_shell_places_operator_metadata_in_title_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_BUILD_ID", "20260408.1703")

    with _client_with_mock_service(monkeypatch) as (client, _mock_service):
        response = client.get("/workflows")

    assert response.status_code == 200
    assert 'class="masthead-title-meta"' in response.text
    assert 'title="MoonMind image version"' in response.text
    assert "v20260408.1703" in response.text
    assert 'class="masthead-meta"' not in response.text

def test_react_shell_hides_title_row_metadata_when_build_id_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_BUILD_ID", raising=False)
    monkeypatch.setenv("MOONMIND_BUILD_ID_PATH", str(tmp_path / "missing-build-id"))

    with _client_with_mock_service(monkeypatch) as (client, _mock_service):
        response = client.get("/workflows")

    assert response.status_code == 200
    assert 'class="masthead-title-meta"' not in response.text

def test_legacy_system_dashboard_route_returns_404(client: TestClient) -> None:
    response = client.get("/workflows/system")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "dashboard_route_not_found"

def test_removed_new_schedule_route_returns_404(client: TestClient) -> None:
    response = client.get("/schedules/new")

    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"

def test_invalid_multi_segment_routes_return_404(client: TestClient) -> None:
    for path in (
        "/workflows/unknown/extra/segment",
        "/workflows/queue/new/extra",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "dashboard_route_not_found"

def test_temporal_source_root_is_not_exposed(client: TestClient) -> None:
    response = client.get("/workflows/temporal")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "dashboard_route_not_found"

def test_temporal_source_subroutes_return_404_until_first_class_source_exists(
    client: TestClient,
) -> None:
    for path in (
        "/workflows/temporal/new",
        f"/workflows/temporal/{uuid4()}",
        "/workflows/temporal/runs",
        "/workflows/new/steps",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "dashboard_route_not_found"

def test_invalid_dashboard_route_returns_404(client: TestClient) -> None:
    response = client.get("/workflows/not-a-valid-dashboard-path/extra")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "dashboard_route_not_found"
    assert detail["message"] == (
        "Workflow console route was not found. Use /workflows, /workflows/new, "
        "/workflows/{workflowId}, /workflows/{workflowId}/steps, "
        "/workflows/{workflowId}/artifacts, or /workflows/{workflowId}/runs."
    )

def test_skills_api_returns_available_skill_ids(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.list_available_skill_names",
        lambda: ("speckit", "speckit-orchestrate"),
    )

    response = client.get("/api/workflows/skills")

    assert response.status_code == 200
    assert response.json() == {
        "items": {
            "worker": ["speckit", "speckit-orchestrate"],
        },
        "legacyItems": [
            {"id": "speckit", "markdown": None},
            {"id": "speckit-orchestrate", "markdown": None},
        ],
    }

def test_skills_api_include_content_reads_legacy_skill_markdown(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_root = tmp_path / "local"
    legacy_root = tmp_path / "legacy"
    skill_dir = legacy_root / "speckit-orchestrate"
    skill_dir.mkdir(parents=True)
    skill_markdown = "# Speckit Orchestrate\n\nRun the full Moon Spec lifecycle."
    (skill_dir / "SKILL.md").write_text(skill_markdown, encoding="utf-8")

    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(local_root),
    )
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_legacy_mirror_root",
        str(legacy_root),
    )
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.list_available_skill_names",
        lambda: ("speckit-orchestrate",),
    )

    response = client.get("/api/workflows/skills?includeContent=true")

    assert response.status_code == 200
    assert response.json()["legacyItems"] == [
        {"id": "speckit-orchestrate", "markdown": skill_markdown},
    ]

def test_create_dashboard_skill_success(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    payload = {
        "name": "MyNewSkill",
        "markdown": "# My New Skill\n\nThis is the skill content...",
    }
    response = client.post("/api/workflows/skills", json=payload)

    assert response.status_code == 201
    assert response.json() == {"status": "success"}

    skill_file = tmp_path / "MyNewSkill" / "SKILL.md"
    assert skill_file.is_file()
    assert skill_file.read_text(encoding="utf-8") == payload["markdown"]

def test_create_dashboard_skill_invalid_name(client: TestClient) -> None:
    payload = {
        "name": "../MyNewSkill",
        "markdown": "# My New Skill\n\nThis is the skill content...",
    }
    response = client.post("/api/workflows/skills", json=payload)

    assert response.status_code == 400
    assert "Invalid skill name" in response.json()["detail"]

def test_create_dashboard_skill_already_exists(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    skill_dir = tmp_path / "ExistingSkill"
    skill_dir.mkdir()

    payload = {
        "name": "ExistingSkill",
        "markdown": "# Existing Skill\n\nContent...",
    }
    response = client.post("/api/workflows/skills", json=payload)

    assert response.status_code == 409
    assert "already exists locally" in response.json()["detail"]

def _skill_zip(entries: dict[str, str]) -> bytes:
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return payload.getvalue()

VALID_SKILL_MARKDOWN = """---
name: zip-skill
description: Uploaded from a zip.
---
# Zip Skill

Use this bundle.
"""

def test_upload_dashboard_skill_zip_saves_valid_bundle(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    payload = _skill_zip(
        {
            "zip-skill/SKILL.md": VALID_SKILL_MARKDOWN,
            "zip-skill/scripts/check.sh": "#!/usr/bin/env bash\nexit 0\n",
            "zip-skill/references/context.md": "Reference material.\n",
        }
    )

    response = client.post(
        "/api/workflows/skills/upload",
        files={"file": ("zip-skill.zip", payload, "application/zip")},
    )

    assert response.status_code == 201
    assert response.json() == {"status": "success", "skill": "zip-skill"}
    assert (tmp_path / "zip-skill" / "SKILL.md").read_text(encoding="utf-8") == VALID_SKILL_MARKDOWN
    assert (tmp_path / "zip-skill" / "scripts" / "check.sh").is_file()
    assert (tmp_path / "zip-skill" / "references" / "context.md").is_file()
    assert not list(tmp_path.glob(".skill-upload-*"))

def test_skill_import_api_saves_valid_bundle_with_result_metadata(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    response = client.post(
        "/api/skills/imports",
        data={"collision_policy": "reject"},
        files={
            "file": (
                "zip-skill.zip",
                _skill_zip(
                    {
                        "zip-skill/skill.md": VALID_SKILL_MARKDOWN,
                        "zip-skill/assets/icon.txt": "asset",
                        "zip-skill/notes.txt": "extra file",
                    }
                ),
                "application/zip",
            )
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "saved"
    assert payload["name"] == "zip-skill"
    assert payload["description"] == "Uploaded from a zip."
    assert payload["skill_id"] == "zip-skill"
    assert payload["version_number"] == 1
    assert payload["warnings"] == []
    assert payload["import_id"]
    assert payload["version_id"]
    assert (tmp_path / "zip-skill" / "SKILL.md").read_text(encoding="utf-8") == VALID_SKILL_MARKDOWN
    assert (tmp_path / "zip-skill" / "assets" / "icon.txt").is_file()
    assert (tmp_path / "zip-skill" / "notes.txt").is_file()

def test_skill_import_api_rejects_missing_frontmatter(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    response = client.post(
        "/api/skills/imports",
        files={
            "file": (
                "zip-skill.zip",
                _skill_zip({"zip-skill/SKILL.md": "# Zip Skill\n\nNo frontmatter."}),
                "application/zip",
            )
        },
    )

    assert response.status_code == 400
    assert "YAML frontmatter" in response.json()["detail"]
    assert not (tmp_path / "zip-skill").exists()

@pytest.mark.parametrize("opening_delimiter", ["----", "---yaml"])
def test_skill_import_api_rejects_malformed_frontmatter_opening_delimiter(
    opening_delimiter: str,
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    response = client.post(
        "/api/skills/imports",
        files={
            "file": (
                "zip-skill.zip",
                _skill_zip(
                    {
                        "zip-skill/SKILL.md": f"""{opening_delimiter}
name: zip-skill
description: Uploaded from a zip.
---
# Zip Skill
"""
                    }
                ),
                "application/zip",
            )
        },
    )

    assert response.status_code == 400
    assert "YAML frontmatter" in response.json()["detail"]
    assert not (tmp_path / "zip-skill").exists()

def test_skill_import_api_rejects_manifest_name_mismatch(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    response = client.post(
        "/api/skills/imports",
        files={
            "file": (
                "zip-skill.zip",
                _skill_zip(
                    {
                        "zip-skill/SKILL.md": """---
name: other-skill
description: Wrong parent.
---
# Zip Skill
"""
                    }
                ),
                "application/zip",
            )
        },
    )

    assert response.status_code == 400
    assert "must match the parent directory" in response.json()["detail"]
    assert not (tmp_path / "zip-skill").exists()

def test_skill_import_api_rejects_existing_skill_by_default(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )
    skill_dir = tmp_path / "zip-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(VALID_SKILL_MARKDOWN, encoding="utf-8")

    response = client.post(
        "/api/skills/imports",
        files={
            "file": (
                "zip-skill.zip",
                _skill_zip({"zip-skill/SKILL.md": VALID_SKILL_MARKDOWN}),
                "application/zip",
            )
        },
    )

    assert response.status_code == 409
    assert "already exists locally" in response.json()["detail"]

def test_skill_import_api_new_version_does_not_overwrite_without_versioned_storage(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )
    skill_dir = tmp_path / "zip-skill"
    skill_dir.mkdir()
    original_markdown = """---
name: zip-skill
description: Original skill.
---
# Original
"""
    (skill_dir / "SKILL.md").write_text(original_markdown, encoding="utf-8")

    response = client.post(
        "/api/skills/imports",
        data={"collision_policy": "new_version"},
        files={
            "file": (
                "zip-skill.zip",
                _skill_zip({"zip-skill/SKILL.md": VALID_SKILL_MARKDOWN}),
                "application/zip",
            )
        },
    )

    assert response.status_code == 409
    assert "versioned skill storage" in response.json()["detail"]
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original_markdown

def test_upload_dashboard_skill_zip_rejects_invalid_root_skill_filename(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    response = client.post(
        "/api/workflows/skills/upload",
        files={
            "file": (
                "my skill.zip",
                _skill_zip({"SKILL.md": VALID_SKILL_MARKDOWN}),
                "application/zip",
            )
        },
    )

    assert response.status_code == 400
    assert "one skill directory" in response.json()["detail"]
    assert not list(tmp_path.iterdir())

def test_upload_dashboard_skill_zip_rejects_invalid_top_level_directory(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    response = client.post(
        "/api/workflows/skills/upload",
        files={
            "file": (
                "bundle.zip",
                _skill_zip({"my skill/SKILL.md": VALID_SKILL_MARKDOWN}),
                "application/zip",
            )
        },
    )

    assert response.status_code == 400
    assert "Invalid skill name" in response.json()["detail"]
    assert not list(tmp_path.iterdir())

def test_upload_dashboard_skill_zip_rejects_missing_skill_markdown(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    response = client.post(
        "/api/workflows/skills/upload",
        files={
            "file": (
                "broken.zip",
                _skill_zip({"broken/README.md": "No skill entrypoint.\n"}),
                "application/zip",
            )
        },
    )

    assert response.status_code == 400
    assert "SKILL.md" in response.json()["detail"]
    assert not (tmp_path / "broken").exists()

def test_upload_dashboard_skill_zip_rejects_path_traversal(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.workflow_console.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    response = client.post(
        "/api/workflows/skills/upload",
        files={
            "file": (
                "unsafe.zip",
                _skill_zip({"unsafe/SKILL.md": VALID_SKILL_MARKDOWN, "../escape.txt": "no\n"}),
                "application/zip",
            )
        },
    )

    assert response.status_code == 400
    assert "unsafe path" in response.json()["detail"]
    assert not (tmp_path / "unsafe").exists()
