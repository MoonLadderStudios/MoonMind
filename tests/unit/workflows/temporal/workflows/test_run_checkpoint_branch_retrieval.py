"""Checkpoint-branch retrieval launch coverage for MoonMind#3514."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _parent_policy() -> dict:
    return {
        "enabled": True,
        "required": False,
        "collections": ["repo", "docs"],
        "filters": {"repository": "MoonMind"},
        "topK": 8,
        "maxContextTokens": 6000,
        "fallbackAllowed": False,
        "staleOverlayAllowed": False,
        "overlayPolicy": "include",
    }


def test_checkpoint_branch_retrieval_override_narrows_launch_policy() -> None:
    result = MoonMindRunWorkflow._narrow_checkpoint_branch_follow_up_retrieval(
        _parent_policy(),
        {
            "enabled": True,
            "required": True,
            "collections": ["docs"],
            "filters": {"repository": "MoonMind", "branch": "feature"},
            "topK": 3,
            "maxContextTokens": 2000,
            "overlayPolicy": "skip",
        },
    )

    assert result["enabled"] is True
    assert result["required"] is True
    assert result["collections"] == ["docs"]
    assert result["filters"] == {
        "repository": "MoonMind",
        "branch": "feature",
    }
    assert result["topK"] == 3
    assert result["maxContextTokens"] == 2000
    assert result["overlayPolicy"] == "skip"


@pytest.mark.parametrize(
    "override, message",
    [
        ({"enabled": True}, "parent-denied"),
        (
            {"enabled": True, "collections": ["private"]},
            "collections exceed",
        ),
        ({"enabled": True, "topK": 9}, "topK exceeds"),
        (
            {"enabled": True, "fallbackAllowed": True},
            "fallbackAllowed exceeds",
        ),
        (
            {"enabled": True, "filters": {"branch": "feature"}},
            "filters relax",
        ),
    ],
)
def test_checkpoint_branch_retrieval_override_rejects_broadening(
    override: dict,
    message: str,
) -> None:
    parent = {} if "parent-denied" in message else _parent_policy()
    with pytest.raises(ValueError, match=message):
        MoonMindRunWorkflow._narrow_checkpoint_branch_follow_up_retrieval(
            parent, override
        )


def test_checkpoint_branch_retrieval_override_can_disable_parent() -> None:
    assert MoonMindRunWorkflow._narrow_checkpoint_branch_follow_up_retrieval(
        _parent_policy(), {"enabled": False}
    ) == {"enabled": False}
