"""Regression tests for MoonLadderStudios/MoonMind#4099.

Covers the one shared workflow-owned title transition (manual ``SetTitle``
renames and automatic ``github-issue-search-and-implement`` enrichment), the
repaired public ``SetTitle`` path, and the opt-in preset declaration.
"""

from __future__ import annotations

import pytest

from moonmind.workflows.executions.title_derivation import (
    TITLE_PROVENANCE_GENERATED,
    TITLE_PROVENANCE_USER_EXPLICIT,
    TitleTransitionState,
    apply_issue_enrichment_title,
    apply_manual_title,
    normalize_display_title,
    render_issue_search_title,
)

BASE_LABEL = "GitHub Issue Search and Implement"


def _generated_state(**overrides) -> TitleTransitionState:
    values = {
        "base_title": BASE_LABEL,
        "display_title": BASE_LABEL,
        "provenance": TITLE_PROVENANCE_GENERATED,
        "revision": 0,
    }
    values.update(overrides)
    return TitleTransitionState(**values)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("My manually chosen workflow title", "My manually chosen workflow title"),
        ("  padded   title  ", "padded title"),
        (123, "123"),
    ],
)
def test_normalize_display_title_accepts_safe_text(raw, expected) -> None:
    assert normalize_display_title(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "a\x00b", "a\x1fb", "a\x7fb", True, False, [], {}],
)
def test_normalize_display_title_rejects_empty_or_unsafe(raw) -> None:
    assert normalize_display_title(raw) is None


def test_normalize_display_title_reuses_display_length_limit() -> None:
    assert normalize_display_title("x" * 500) == "x" * 150


def test_render_issue_search_title_uses_frozen_base_and_number() -> None:
    assert (
        render_issue_search_title(BASE_LABEL, 4054) == f"{BASE_LABEL}: #4054"
    )


@pytest.mark.parametrize("number", [0, -3, "nope", None, True])
def test_render_issue_search_title_rejects_bad_numbers(number) -> None:
    assert render_issue_search_title(BASE_LABEL, number) is None


def test_render_issue_search_title_rejects_missing_base() -> None:
    assert render_issue_search_title("   ", 4054) is None


def test_auto_enrichment_renders_from_base_never_appends() -> None:
    state = _generated_state(
        display_title=f"{BASE_LABEL}: #1111",
        revision=2,
    )
    result = apply_issue_enrichment_title(state, 4054, expected_revision=2)
    assert result.changed is True
    assert result.state.display_title == f"{BASE_LABEL}: #4054"
    assert result.state.revision == 3


def test_auto_enrichment_pending_selection_leaves_base_unchanged() -> None:
    state = _generated_state()
    for bad in (0, -1, None, "nope"):
        result = apply_issue_enrichment_title(state, bad, expected_revision=0)
        assert result.changed is False
        assert result.state == state


def test_auto_enrichment_is_idempotent_for_duplicates() -> None:
    state = _generated_state()
    first = apply_issue_enrichment_title(state, 4054, expected_revision=0)
    assert first.changed is True
    second = apply_issue_enrichment_title(
        first.state, 4054, expected_revision=first.state.revision
    )
    assert second.changed is False
    assert second.reason == "no-op"
    assert second.state == first.state


def test_auto_enrichment_protects_explicit_titles() -> None:
    state = _generated_state(
        display_title="My manually chosen workflow title",
        provenance=TITLE_PROVENANCE_USER_EXPLICIT,
        revision=4,
    )
    result = apply_issue_enrichment_title(state, 4054, expected_revision=4)
    assert result.changed is False
    assert result.reason == "explicit"
    assert result.state == state


def test_auto_enrichment_rejects_stale_revisions() -> None:
    state = _generated_state(revision=3)
    result = apply_issue_enrichment_title(state, 4054, expected_revision=1)
    assert result.changed is False
    assert result.reason == "stale"
    assert result.state == state


def test_manual_rename_marks_explicit_even_for_same_text() -> None:
    enriched = f"{BASE_LABEL}: #4054"
    state = _generated_state(display_title=enriched, revision=1)
    result = apply_manual_title(state, enriched)
    assert result.changed is True
    assert result.state.display_title == enriched
    assert result.state.provenance == TITLE_PROVENANCE_USER_EXPLICIT
    assert result.state.revision == 2


def test_manual_rename_noop_does_not_bump_revision() -> None:
    state = _generated_state(
        display_title="Chosen",
        provenance=TITLE_PROVENANCE_USER_EXPLICIT,
        revision=5,
    )
    result = apply_manual_title(state, "Chosen")
    assert result.changed is False
    assert result.state == state


@pytest.mark.parametrize("raw", ["", "   ", "bad\x00title"])
def test_manual_rename_rejects_empty_or_unsafe(raw) -> None:
    with pytest.raises(ValueError):
        apply_manual_title(_generated_state(), raw)


def test_manual_then_auto_keeps_explicit_title() -> None:
    manual = apply_manual_title(_generated_state(), "Operator choice")
    assert manual.changed is True
    auto = apply_issue_enrichment_title(
        manual.state, 4054, expected_revision=manual.state.revision
    )
    assert auto.changed is False
    assert manual.state.display_title == "Operator choice"
