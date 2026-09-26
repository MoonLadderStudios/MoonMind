"""Publication-spelling coverage for MoonLadderStudios/MoonMind#3621.

The checkpoint-branch turn owner must reuse one shared publication compiler
for current and historical spellings instead of an inline literal set, and a
work branch must never gain a new publication grant from an ``auto`` intent.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.workspace_publication import (
    BranchPublishModeError,
    branch_publish_mode_for_destination,
    compile_branch_publish_mode,
)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        (None, "none"),
        ("", "none"),
        ("none", "none"),
        ("None", "none"),
        ("branch", "branch"),
        ("Branch", "branch"),
        ("pull_request", "pull_request"),
        ("PULL_REQUEST", "pull_request"),
        ("pull-request", "pull_request"),
        ("pullrequest", "pull_request"),
        ("pr", "pull_request"),
        ("PR", "pull_request"),
        # A work branch is not a new publication grant: skill/managed-owned
        # ``auto`` intent resolves to the read-only default instead of
        # promoting the branch to branch/PR publication.
        ("auto", "none"),
    ],
)
def test_compile_branch_publish_mode_accepts_current_and_historical_spellings(
    raw: object, canonical: str
) -> None:
    assert compile_branch_publish_mode(raw) == canonical


@pytest.mark.parametrize("raw", ["force_merge", "merge", "commit", "yes", 7])
def test_compile_branch_publish_mode_rejects_unknown_spellings(raw: object) -> None:
    with pytest.raises(BranchPublishModeError) as exc_info:
        compile_branch_publish_mode(raw)

    assert exc_info.value.code == "publish_intent_unsupported"


def test_branch_publish_mode_for_destination_matches_shared_publisher() -> None:
    # The shared workspace publisher acts on {"branch", "pr"}; the branch
    # canonical "pull_request" spelling must be translated at the handoff so
    # an authorized PR intent is not silently skipped downstream.
    assert branch_publish_mode_for_destination("none") == "none"
    assert branch_publish_mode_for_destination("branch") == "branch"
    assert branch_publish_mode_for_destination("pull_request") == "pr"
