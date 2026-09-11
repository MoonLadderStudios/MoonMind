"""Workflow-boundary tests for MoonLadderStudios/MoonMind#4099.

Drives the actual resolver-to-title trigger
``_maybe_enrich_title_from_trusted_issue`` with controlled accepted payloads.
Hermetic: no live Temporal server; memo/search publication is stubbed.
"""

from __future__ import annotations

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

BASE_LABEL = "GitHub Issue Search and Implement"


def _opt_in_workflow(
    *,
    base: str | None = BASE_LABEL,
    title: str | None = BASE_LABEL,
    provenance: str = "generated",
    revision: int = 0,
    opt_in: bool = True,
) -> MoonMindRunWorkflow:
    wf = MoonMindRunWorkflow()
    wf._title = title
    wf._title_base = base
    wf._title_provenance = provenance
    wf._title_revision = revision
    wf._title_target = None
    wf._title_source = "preset_template" if provenance == "generated" else "user_explicit"
    wf._title_confidence = "medium" if provenance == "generated" else "high"
    if opt_in:
        wf._original_input_payload = {
            "initialParameters": {
                "workflow": {
                    "titleEnrichment": {"enabled": True},
                    "taskTemplate": {"slug": "github-issue-search-and-implement"},
                }
            }
        }
    else:
        wf._original_input_payload = {
            "initialParameters": {
                "workflow": {"taskTemplate": {"slug": "other-preset"}}
            }
        }
    # Keep hermetic: _update_memo/_update_search_attributes call
    # workflow.patched/upsert outside the event loop.
    wf._update_memo = lambda: None  # type: ignore[method-assign]
    wf._update_search_attributes = lambda: None  # type: ignore[method-assign]
    return wf


def _accepted_outputs(number=4054) -> dict:
    return {
        "trustedSource": "moonmind.github.get_issue",
        "repository": "MoonLadderStudios/MoonMind",
        "issueNumber": number,
    }


def test_accepted_resolver_result_enriches_display_title() -> None:
    wf = _opt_in_workflow()
    changed = wf._maybe_enrich_title_from_trusted_issue(_accepted_outputs(4054))
    assert changed is True
    assert wf._title == f"{BASE_LABEL}: #4054"
    assert wf._title_base == BASE_LABEL
    assert wf._title_provenance == "generated"
    assert wf._title_revision == 1
    assert wf._title_target == {
        "provider": "github",
        "repository": "MoonLadderStudios/MoonMind",
        "issueNumber": 4054,
        "source": "moonmind.github.get_issue",
    }


def test_untrusted_source_never_triggers() -> None:
    wf = _opt_in_workflow()
    outputs = _accepted_outputs(4054)
    outputs["trustedSource"] = "agent.prose"
    assert wf._maybe_enrich_title_from_trusted_issue(outputs) is False
    assert wf._title == BASE_LABEL
    assert wf._title_revision == 0


def test_missing_or_invalid_number_never_triggers() -> None:
    for bad in (None, 0, -3, "nope", True):
        wf = _opt_in_workflow()
        assert wf._maybe_enrich_title_from_trusted_issue(_accepted_outputs(bad)) is False
        assert wf._title == BASE_LABEL
        assert wf._title_revision == 0


def test_non_opt_in_preset_never_triggers() -> None:
    wf = _opt_in_workflow(opt_in=False)
    assert wf._maybe_enrich_title_from_trusted_issue(_accepted_outputs(4054)) is False
    assert wf._title == BASE_LABEL
    assert wf._title_revision == 0


def test_explicit_title_is_protected() -> None:
    wf = _opt_in_workflow(
        title="Operator choice", provenance="user_explicit", revision=2
    )
    assert wf._maybe_enrich_title_from_trusted_issue(_accepted_outputs(4054)) is False
    assert wf._title == "Operator choice"
    assert wf._title_revision == 2


def test_duplicate_accepted_result_is_idempotent() -> None:
    wf = _opt_in_workflow()
    assert wf._maybe_enrich_title_from_trusted_issue(_accepted_outputs(4054)) is True
    revision = wf._title_revision
    assert wf._maybe_enrich_title_from_trusted_issue(_accepted_outputs(4054)) is False
    assert wf._title == f"{BASE_LABEL}: #4054"
    assert wf._title_revision == revision
