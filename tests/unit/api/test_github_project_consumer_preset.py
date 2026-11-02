"""The project preset expands through the same implementation and policy owner."""

from pathlib import Path

import pytest

from api_service.services.presets.catalog import PresetCatalogService
from moonmind.workflows.executions.preset_readiness import (
    github_issue_search_scope_refresh_needed,
)
from tests.unit.api.test_batch_github_workflows_preset import _catalog_db


@pytest.mark.asyncio
async def test_project_consumer_expands_native_selection_and_shared_implementation(
    tmp_path,
):
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(seed_dir=Path("api_service/data/presets"))
            expanded = await catalog.expand_template(
                slug="github-project-implement",
                inputs={
                    "project_url": "https://github.com/orgs/MoonLadderStudios/projects/3/views/1",
                    "source_columns": "Ready\nBacklog",
                },
                context={"repository": "MoonLadderStudios/MoonMind"},
            )
    loader = expanded["steps"][0]["tool"]
    assert loader["id"] == "github.load_issue_preset_brief"
    assert loader["inputs"]["repository"] == "MoonLadderStudios/MoonMind"
    assert loader["inputs"]["includeAllAuthors"] is True
    assert loader["inputs"]["projectSource"]["columns"] == "Ready\nBacklog"
    assert loader["inputs"]["projectSource"]["url"].endswith("/views/1")
    assert any(
        step.get("tool", {}).get("id") == "github.update_issue_status"
        for step in expanded["steps"]
    )
    assert sorted(expanded["capabilities"]) == ["docker", "gh", "git"]


@pytest.mark.asyncio
@pytest.mark.parametrize("inputs", [{}, {"project_url": ""}])
async def test_project_preset_defaults_to_discovery_for_the_selected_repository(
    tmp_path, inputs
):
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(seed_dir=Path("api_service/data/presets"))
            expanded = await catalog.expand_template(
                slug="github-project-implement",
                inputs=inputs,
                context={"repository": "another-org/another-repo"},
            )
    loader_inputs = expanded["steps"][0]["tool"]["inputs"]
    assert loader_inputs["repository"] == "another-org/another-repo"
    assert loader_inputs["projectSource"]["url"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("all_authors", [False, True])
async def test_project_schedule_accepts_its_expanded_author_choice(
    tmp_path, all_authors
):
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(seed_dir=Path("api_service/data/presets"))
            expanded = await catalog.expand_template(
                slug="github-project-implement",
                inputs={"include_all_authors": all_authors},
                context={"repository": "another-org/another-repo"},
            )
    parameters = {
        "system": {"recurrence": {"definitionId": "project-schedule"}},
        "workflow": {"appliedStepTemplates": [expanded["appliedTemplate"]]},
    }
    assert github_issue_search_scope_refresh_needed(parameters) is None
