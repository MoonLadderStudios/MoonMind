"""MoonLadderStudios/MoonMind#5190: admission for read-only period evidence."""

import copy

import pytest
from pydantic import ValidationError

from moonmind.security.assessments import AssessmentRequest


def request(**selection):
    return {
        "scope": {
            "assets": ["acme/repo"],
            "exclusions": [],
            "repositoryExceptions": [],
        },
        "tools": ["github-change-control"],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "githubChangeControl": {
            "repositories": ["acme/repo"],
            "environments": ["production"],
            "since": "2026-11-01T00:00:00Z",
            "until": "2026-11-06T00:00:00Z",
            "requirementIds": ["soc2.CC8.1", "iso27001.A.8.32"],
            **selection,
        },
    }


def test_period_selection_uses_repository_authority_and_explicit_bounds():
    admitted = AssessmentRequest.model_validate(request())
    assert admitted.github_change_control.repositories == ["acme/repo"]
    assert admitted.github_change_control.until > admitted.github_change_control.since
    assert admitted.credential_refs == admitted.network_refs == []


@pytest.mark.parametrize(
    "selection",
    [
        {"repositories": []},
        {"repositories": ["acme/repo", "ACME/REPO"]},
        {"repositories": ["acme/../repo"]},
        {"repositories": ["acme/other"]},
        {"environments": []},
        {"environments": ["production", "production"]},
        {"since": "2026-11-01T00:00:00"},
        {"until": "2026-11-01T00:00:00Z"},
        {"maxRequests": 0},
        {"maxPages": 0},
        {"maxItems": 0},
        {"maxBytes": 0},
        {"maxSeconds": 61},
        {"sampleSize": 0},
    ],
)
def test_invalid_or_out_of_scope_selection_is_rejected(selection):
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate(request(**selection))


def test_tool_and_selection_must_match():
    payload = copy.deepcopy(request())
    payload["tools"] = []
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate(payload)
