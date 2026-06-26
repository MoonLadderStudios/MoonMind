from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from api_service.api.routers import settings as settings_router
from api_service.auth_providers import get_current_user
from api_service.main import app

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

SETTINGS_USER_DEP = get_current_user()


@pytest.fixture
def settings_user_override():
    user = SimpleNamespace(
        id=uuid4(),
        email="settings-user@example.com",
        is_superuser=True,
        settings_permissions={
            "settings.catalog.read",
            "settings.effective.read",
            "settings.workspace.write",
        },
        workspace_id=uuid4(),
    )
    app.dependency_overrides[SETTINGS_USER_DEP] = lambda: user
    try:
        yield user
    finally:
        app.dependency_overrides.pop(SETTINGS_USER_DEP, None)


async def test_github_token_probe_route_targets_selected_repo(monkeypatch, settings_user_override):
    seen: dict[str, object] = {}

    async def _fake_probe(**kwargs):
        seen.update(kwargs)
        return {
            "repo": kwargs["repo"],
            "mode": kwargs["mode"],
            "credentialSource": {
                "sourceKind": "direct_env",
                "sourceName": "GITHUB_TOKEN",
                "resolved": True,
            },
            "repositoryAccessible": True,
            "defaultBranchAccessible": True,
            "pullRequestAccessible": True,
            "permissionChecklist": [
                {
                    "permission": "Contents",
                    "level": "write",
                    "required": True,
                    "status": "passed",
                }
            ],
            "diagnostics": [],
            "limitations": [],
        }

    monkeypatch.setattr(settings_router, "probe_github_token", _fake_probe)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/settings/github/token-probe",
            json={"repo": "owner/repo", "mode": "publish", "baseBranch": "main"},
        )

    assert response.status_code == 200
    assert seen == {"repo": "owner/repo", "mode": "publish", "base_branch": "main"}
    body = response.json()
    assert body["repo"] == "owner/repo"
    assert body["permissionChecklist"][0]["permission"] == "Contents"


async def test_settings_token_ref_reaches_canonical_github_resolver(monkeypatch):
    from moonmind.auth.github_credentials import resolve_github_credential

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("WORKFLOW_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN_SECRET_REF", raising=False)
    monkeypatch.delenv("WORKFLOW_GITHUB_TOKEN_SECRET_REF", raising=False)
    monkeypatch.setenv("MOONMIND_GITHUB_TOKEN_REF", "db://github-pat-main")

    async def _fake_secret_ref(ref: str) -> str:
        assert ref == "db://github-pat-main"
        return "resolved-token"

    monkeypatch.setattr(
        "moonmind.auth.github_credentials._resolve_secret_ref",
        _fake_secret_ref,
    )

    resolved = await resolve_github_credential(repo="owner/repo")

    assert resolved.token == "resolved-token"
    assert resolved.source_name == "MOONMIND_GITHUB_TOKEN_REF"
    assert resolved.repo == "owner/repo"
