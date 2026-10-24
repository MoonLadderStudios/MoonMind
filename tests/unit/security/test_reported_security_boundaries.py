"""Regression coverage for untrusted GitHub identifiers and policy diagnostics."""

import pytest

from moonmind.auth.github_app import _numeric_key
from moonmind.workflows.temporal.github_issue_lifecycle import is_workflow_status_like


@pytest.mark.timeout(2)
def test_installation_identifier_with_long_invalid_suffix_is_bounded():
    identifier = "installation:" + "1" * 50_000 + "x"
    assert _numeric_key(identifier) == identifier


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("installation:123", "123"),
        (" 123 ", "123"),
        ("app:١٢", "١٢"),
        ("app:x", "app:x"),
    ],
)
def test_numeric_identifiers_keep_supported_suffixes(value, expected):
    assert _numeric_key(value) == expected


@pytest.mark.timeout(2)
def test_status_label_with_long_whitespace_and_invalid_tail_is_bounded():
    assert not is_workflow_status_like("status" + " " * 50_000 + "x\nx")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("status ready", True),
        ("status/-_ unknown", True),
        ("status\t\nready", True),
        ("statuspage", False),
        ("status x\nx", False),
        ("status: in-progress", False),
    ],
)
def test_status_like_labels_preserve_classification(value, expected):
    assert is_workflow_status_like(value) is expected


def test_policy_validation_does_not_expose_exception_details(monkeypatch):
    from api_service.services import omnigent_policies
    from moonmind.omnigent.policies import PolicyDocument
    from tests.unit.omnigent.test_policy_authority import policy_document

    def reject(_document):
        raise ValueError("private configuration at /srv/operator/hidden-policy.json")

    monkeypatch.setattr(omnigent_policies, "require_explicit_policy_resources", reject)
    validation, _ = omnigent_policies.validate_policy(
        PolicyDocument.model_validate(policy_document())
    )
    diagnostic = next(
        d
        for d in validation["diagnostics"]
        if d["code"] == "OMNIGENT_CPU_LIMIT_REQUIRED"
    )
    assert (
        diagnostic["message"] == "resources.cpuMillis must be a positive explicit limit"
    )
