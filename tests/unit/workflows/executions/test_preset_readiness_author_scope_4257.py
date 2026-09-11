"""Saved-plan author-scope compatibility for MoonLadderStudios/MoonMind#4257.

Pure-function coverage for the frozen-schedule refresh-required path: a
pre-change GitHub issue-search application without an author-scope choice
must stop before new selection, while refreshed true/false choices, other
presets, and malformed provenance stay on their existing paths.
"""

from __future__ import annotations

from moonmind.workflows.executions.preset_readiness import (
    github_issue_search_scope_refresh_needed,
)

SEARCH = "github-issue-search-and-implement"


def _parameters(applied, definition_id="def-1"):
    return {
        "system": {"recurrence": {"definitionId": definition_id}},
        "task": {"appliedStepTemplates": applied},
    }


def _applied(inputs, slug=SEARCH, scope="global"):
    entry: dict = {"slug": slug, "scope": scope}
    if inputs is not None:
        entry["inputs"] = inputs
    return entry


def test_missing_scope_choice_requires_refresh() -> None:
    payload = github_issue_search_scope_refresh_needed(
        _parameters([_applied(None)])
    )
    assert payload is not None
    assert payload["status"] == "refresh_required"
    assert payload["code"] == "github_issue_search_author_scope_stale"
    assert payload["missingInputs"] == ["include_all_authors"]
    assert payload["definitionId"] == "def-1"
    assert payload["presets"] == [
        {"slug": SEARCH, "scope": "global", "reason": "author_scope_choice_missing"}
    ]
    assert "plan refresh" in payload["message"]
    assert "Include issues created by other users" in payload["message"]


def test_empty_inputs_require_refresh() -> None:
    assert (
        github_issue_search_scope_refresh_needed(_parameters([_applied({})]))
        is not None
    )


def test_explicit_false_and_true_need_no_refresh() -> None:
    for value in (False, True):
        assert (
            github_issue_search_scope_refresh_needed(
                _parameters([_applied({"include_all_authors": value})])
            )
            is None
        )


def test_camel_case_authored_choice_needs_no_refresh() -> None:
    assert (
        github_issue_search_scope_refresh_needed(
            _parameters([_applied({"includeAllAuthors": True})])
        )
        is None
    )


def test_other_presets_need_no_scope_refresh() -> None:
    assert (
        github_issue_search_scope_refresh_needed(
            _parameters([_applied(None, slug="jira-orchestrate")])
        )
        is None
    )


def test_nested_composition_without_scope_requires_refresh() -> None:
    parameters = _parameters(
        [
            {
                "slug": "parent",
                "composition": {
                    "slug": "parent",
                    "includes": [{"slug": SEARCH, "inputs": {}}],
                },
            }
        ]
    )
    payload = github_issue_search_scope_refresh_needed(parameters)
    assert payload is not None
    assert payload["presets"][0]["slug"] == SEARCH


def test_missing_task_or_malformed_provenance_needs_no_scope_decision() -> None:
    assert github_issue_search_scope_refresh_needed({}) is None
    assert github_issue_search_scope_refresh_needed({"task": {}}) is None
    assert (
        github_issue_search_scope_refresh_needed(
            {"task": {"appliedStepTemplates": ["invalid"]}}
        )
        is None
    )


def test_workflow_node_is_supported() -> None:
    parameters = {
        "workflow": {"appliedStepTemplates": [_applied(None)]},
    }
    assert github_issue_search_scope_refresh_needed(parameters) is not None
