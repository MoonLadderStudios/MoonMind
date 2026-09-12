"""Dashboard-visible input contracts preserve the selected completion branch."""

from pathlib import Path

import pytest

from api_service.services.presets.catalog import PresetCatalogService
from tests.unit.api.test_presets_service import template_db


PRESETS = (
    "issue-implement-assessment",
    "issue-implement-work-pr",
    "github-issue-implement",
    "github-issue-search-and-implement",
    "github-issue-orchestrate",
    "jira-implement",
    "jira-orchestrate",
)
ROOT = Path(__file__).resolve().parents[3]


def _completion_refs(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"completion_target_ref", "completionTargetRef"}:
                yield child
            else:
                yield from _completion_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _completion_refs(child)


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", PRESETS)
@pytest.mark.parametrize("target", [None, "", "refs/heads/release/4265"])
async def test_visible_completion_target_survives_catalog_expansion(
    tmp_path, slug, target
):
    async with template_db(tmp_path) as sessions:
        async with sessions() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(seed_dir=ROOT / "api_service/data/presets")
            detail = await catalog.get_template(
                slug=slug, scope="global", scope_ref=None
            )
            properties = detail["inputSchema"]["properties"]
            assert properties["completion_target_ref"]["type"] == "string"
            assert "completion_target_ref" not in detail["inputSchema"].get(
                "required", []
            )
            assert detail["uiSchema"]["completion_target_ref"] == {
                "widget": "text",
                "advanced": True,
            }
            assert detail["defaults"].get("completion_target_ref", "") == ""

            # These are the values the form can submit from its authoritative
            # contract, including issue-picker objects and internal preset inputs.
            supplied = {
                "github_issue": {"repository": "example/repo", "number": 4265},
                "jira_issue": {"key": "MM-4265"},
                "issue_provider": "github",
                "issue_ref": "example/repo#4265",
                "brief_artifact_path": "artifacts/brief.json",
                "assessment_artifact_path": "artifacts/assessment.json",
                "pr_artifact_path": "artifacts/pr.json",
                "verify_artifact_path": "artifacts/verification.json",
            }
            inputs = {
                key: value for key, value in supplied.items() if key in properties
            }
            if target is not None:
                inputs["completion_target_ref"] = target
            expanded = await catalog.expand_template(
                slug=slug,
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context={"repository": "example/repo"},
            )

    refs = list(_completion_refs(expanded["steps"]))
    assert refs, "At least the assessment or verifier must receive the policy"
    assert set(refs) == {target or ""}
