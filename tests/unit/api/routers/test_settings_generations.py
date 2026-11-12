"""Settings optimistic versions across real HTTP requests and persistence."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from api_service.api.routers import settings as settings_router
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    saved_profile_submission as _saved_profile_submission,
)

saved_profile_submission = _saved_profile_submission
RUNTIME = "workflow.default_runtime"
PROFILE = "workflow.default_provider_profile_ref"


@pytest_asyncio.fixture
async def defaults_context(saved_profile_submission, monkeypatch):
    context = saved_profile_submission
    context.client._transport.app.include_router(
        settings_router.router, prefix="/api/v1"
    )
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        async_sessionmaker(context.session.bind, expire_on_commit=False),
    )
    return context


async def catalog(context):
    response = await context.client.get(
        "/api/v1/settings/catalog?section=user-workspace"
    )
    assert response.status_code == 200, response.text
    return {
        item["key"]: item
        for group in response.json()["categories"].values()
        for item in group
    }


async def save(context, runtime, profile, *, versions=None):
    if versions is None:
        descriptors = await catalog(context)
        versions = {
            key: descriptors[key]["value_version"] for key in (RUNTIME, PROFILE)
        }
    return await context.client.patch(
        "/api/v1/settings",
        json={
            "changes": {RUNTIME: runtime, PROFILE: profile},
            "expected_versions": versions,
            "reason": "Updated from Providers & Secrets.",
        },
    )


@pytest.mark.asyncio
async def test_pair_save_rejects_editor_loaded_before_first_override(defaults_context):
    context = defaults_context
    for key in (RUNTIME, PROFILE):
        response = await context.client.delete(f"/api/v1/settings/{key}")
        assert response.status_code == 200, response.text
    before = await catalog(context)
    versions = {key: before[key]["value_version"] for key in (RUNTIME, PROFILE)}
    first = await save(context, "claude_code", "saved-claude_code", versions=versions)
    assert first.status_code == 200, first.text
    stale = await save(context, "codex_cli", None, versions=versions)
    assert stale.status_code == 409, stale.text
    after = await catalog(context)
    assert after[RUNTIME]["effective_value"] == "claude_code"
    assert after[PROFILE]["effective_value"] == "saved-claude_code"


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_key", [RUNTIME, PROFILE])
async def test_advanced_reset_retains_generation_without_pinning_inherited_value(
    defaults_context, monkeypatch, reset_key
):
    context = defaults_context
    initial = await save(context, "claude_code", "saved-claude_code")
    assert initial.status_code == 200, initial.text
    before = await catalog(context)
    versions = {key: before[key]["value_version"] for key in (RUNTIME, PROFILE)}
    reset = await context.client.delete(f"/api/v1/settings/{reset_key}")
    assert reset.status_code == 200, reset.text
    assert reset.json()["source"] != "instance_override"
    assert reset.json()["value_version"] > versions[reset_key]
    # Recreating an override must not reuse a generation held by an old editor.
    new = await save(context, "codex_cli", "alternative-codex_cli")
    assert new.status_code == 200, new.text
    stale = await save(context, "claude_code", "saved-claude_code", versions=versions)
    assert stale.status_code == 409, stale.text


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_key", [RUNTIME, PROFILE])
async def test_pair_version_conflict_is_atomic_when_other_value_is_unchanged(
    defaults_context, concurrent_key
):
    context = defaults_context
    assert (await save(context, "codex_cli", None)).status_code == 200
    descriptors = await catalog(context)
    versions = {key: descriptors[key]["value_version"] for key in (RUNTIME, PROFILE)}
    concurrent_value = (
        "omnigent" if concurrent_key == RUNTIME else "alternative-codex_cli"
    )
    concurrent = await context.client.patch(
        "/api/v1/settings",
        json={
            "changes": {concurrent_key: concurrent_value},
            "expected_versions": {concurrent_key: versions[concurrent_key]},
        },
    )
    assert concurrent.status_code == 200, concurrent.text
    stale = await save(context, "codex_cli", "saved-codex_cli", versions=versions)
    assert stale.status_code == 409, stale.text
    after = await catalog(context)
    assert after[RUNTIME]["effective_value"] == (
        concurrent_value if concurrent_key == RUNTIME else "codex_cli"
    )
    assert after[PROFILE]["effective_value"] == (
        concurrent_value if concurrent_key == PROFILE else None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_concurrent_first_insert_and_update_have_one_winner(
    defaults_context, existing
):
    import asyncio

    context = defaults_context
    if existing:
        assert (await save(context, "codex_cli", None)).status_code == 200
    before = await catalog(context)
    versions = {key: before[key]["value_version"] for key in (RUNTIME, PROFILE)}
    responses = await asyncio.gather(
        save(context, "claude_code", "saved-claude_code", versions=versions),
        save(context, "omnigent", None, versions=versions),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(
        response.json()["values"]
        for response in responses
        if response.status_code == 200
    )
    after = await catalog(context)
    for key in (RUNTIME, PROFILE):
        assert after[key]["effective_value"] == winner[key]["value"]
        assert after[key]["value_version"] == versions[key] + 1


@pytest.mark.asyncio
async def test_reset_reads_live_defaults_and_retains_generation_everywhere(
    defaults_context, monkeypatch
):
    from moonmind.config.settings import settings

    context = defaults_context
    assert (await save(context, "claude_code", None)).status_code == 200
    reset = await context.client.delete(f"/api/v1/settings/{RUNTIME}")
    generation = reset.json()["value_version"]
    monkeypatch.setenv("WORKFLOW_DEFAULT_RUNTIME", "omnigent")
    inherited = (await catalog(context))[RUNTIME]
    assert inherited["effective_value"] == "omnigent"
    assert inherited["source"] == "environment"
    assert inherited["value_version"] == generation
    assert inherited["active"] is True
    assert inherited["pending_value"] is None
    monkeypatch.delenv("WORKFLOW_DEFAULT_RUNTIME")
    monkeypatch.delenv("MOONMIND_DEFAULT_RUNTIME", raising=False)
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    inherited = (await catalog(context))[RUNTIME]
    assert inherited["effective_value"] == "codex_cli"
    assert inherited["source"] == "config_file"
    repeated = await context.client.delete(f"/api/v1/settings/{RUNTIME}")
    assert repeated.json()["value_version"] == generation
    effective = await context.client.get(f"/api/v1/settings/effective/{RUNTIME}")
    assert effective.json()["value_version"] == generation
    diagnostics = await context.client.get("/api/v1/settings/diagnostics")
    assert diagnostics.json()["values"][RUNTIME]["value_version"] == generation
    proposal = {
        "changes": {RUNTIME: "omnigent"},
        "expected_versions": {RUNTIME: generation - 1},
    }
    for endpoint in ("validate", "preview"):
        response = await context.client.post(
            f"/api/v1/settings/{endpoint}", json=proposal
        )
        assert response.status_code == 409, response.text
        assert response.json()["details"]["details"]["current_version"] == generation
    proposal["expected_versions"][RUNTIME] = generation
    preview = await context.client.post("/api/v1/settings/preview", json=proposal)
    assert preview.status_code == 200, preview.text
    assert preview.json()["diffs"][0]["before"]["value_version"] == generation
    audit = await context.client.get("/api/v1/settings/audit", params={"key": RUNTIME})
    events = audit.json()["items"]
    assert [event["event_type"] for event in events].count(
        "settings.override.reset"
    ) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("companion", [RUNTIME, PROFILE])
async def test_unchanged_companion_precondition_rejects_stale_atomic_write(
    defaults_context, companion
):
    context = defaults_context
    assert (await save(context, "codex_cli", None)).status_code == 200
    before = await catalog(context)
    versions = {key: before[key]["value_version"] for key in (RUNTIME, PROFILE)}
    changed_key = PROFILE if companion == RUNTIME else RUNTIME
    concurrent = await context.client.patch(
        "/api/v1/settings",
        json={
            "changes": {
                companion: "claude_code" if companion == RUNTIME else "saved-codex_cli"
            },
            "expected_versions": {companion: versions[companion]},
        },
    )
    assert concurrent.status_code == 200
    proposal = {
        "changes": {changed_key: None if changed_key == PROFILE else "omnigent"},
        "expected_versions": versions,
    }
    for endpoint in ("validate", "preview"):
        result = await context.client.post(
            f"/api/v1/settings/{endpoint}", json=proposal
        )
        assert result.status_code == 409, result.text
    result = await context.client.patch("/api/v1/settings", json=proposal)
    assert result.status_code == 409, result.text
    after = await catalog(context)
    assert after[changed_key]["value_version"] == versions[changed_key]
    assert (
        after[changed_key]["effective_value"] == before[changed_key]["effective_value"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("locked", [RUNTIME, PROFILE])
async def test_locked_companion_is_a_precondition_not_a_write(
    defaults_context, monkeypatch, locked
):
    from dataclasses import replace

    from api_service.services.settings_catalog import _REGISTRY, SettingsCatalogService

    context = defaults_context
    registry = tuple(
        replace(
            entry,
            operator_lock_reason="Controlled by deployment policy.",
            operator_locked_value="codex_cli" if locked == RUNTIME else None,
        )
        if entry.key == locked
        else entry
        for entry in _REGISTRY
    )
    monkeypatch.setattr(
        settings_router,
        "SettingsCatalogService",
        lambda **kwargs: SettingsCatalogService(registry=registry, **kwargs),
    )
    before = await catalog(context)
    changed = PROFILE if locked == RUNTIME else RUNTIME
    proposal = {
        "changes": {
            changed: "alternative-codex_cli" if changed == PROFILE else "claude_code"
        },
        "expected_versions": {
            key: before[key]["value_version"] for key in (RUNTIME, PROFILE)
        },
    }
    for endpoint in ("validate", "preview"):
        result = await context.client.post(
            f"/api/v1/settings/{endpoint}", json=proposal
        )
        assert result.status_code == 200, result.text
        assert result.json()["accepted"]
    result = await context.client.patch("/api/v1/settings", json=proposal)
    assert result.status_code == 200, result.text
    assert set(result.json()["values"]) == {changed}
    after = await catalog(context)
    assert after[locked]["value_version"] == before[locked]["value_version"]
    assert after[locked]["effective_value"] == before[locked]["effective_value"]
    assert after[locked]["source"] == "operator_lock"
