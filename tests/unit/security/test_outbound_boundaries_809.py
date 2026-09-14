"""MoonMind#809: per-surface enabled-mode outbound-scan boundary matrix.

Each test uses the real canonical scanner with synthetic secrets only, and
asserts the scan precedes the side effect: the downstream send/push is never
invoked on block, and the raw synthetic value appears nowhere in the raised
error, diagnostics, evidence, or audit metadata.

Caller matrix row (entrypoint | scanner/extractor | policy source | test):
- PR/issue comments (Jira add_comment) | scan_outbound_text | explicit True | test_jira_comment_blocks_before_send
- Provider follow-up messages (Jules send_message) | scan_outbound_text | operator env True | test_jules_send_message_blocks_before_post
- Native queued/steered messages | scan_native_outbound digest+idempotency | explicit True | test_native_allow_bound_to_digest_and_key
- Push bundles | scan_outbound_bundle + push_scan_coverage_error | explicit True | test_push_bundle_and_coverage_fail_closed
- Operator/provider messages + notifications | scan_outbound_text exact-content | explicit True | test_operator_message_exact_content_and_redaction
"""

from __future__ import annotations

import pytest

from moonmind.config.settings import AtlassianSettings, JiraSettings
from moonmind.integrations.jira.errors import JiraToolError
from moonmind.integrations.jira.models import AddCommentRequest
from moonmind.integrations.jira.tool import JiraToolService
from moonmind.omnigent.native_outbound_scan import (
    NativeScanBlockedError,
    NativeScanEnforcementError,
    NativeScanSurface,
    canonical_payload_digest,
    scan_native_outbound,
)
from moonmind.schemas.jules_models import JulesSendMessageRequest
from moonmind.security.outbound_scan import (
    canonical_outbound_digest,
    push_scan_coverage_error,
    scan_outbound_bundle,
    scan_outbound_text,
)
from moonmind.workflows.adapters.jules_client import JulesClient, JulesClientError


class _StubJiraService(JiraToolService):
    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self.calls: list[dict] = []

    async def _request_json(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return {"id": "must-not-post"}


def _jira_settings() -> AtlassianSettings:
    return AtlassianSettings(
        atlassian_api_key=None,
        atlassian_api_key_secret_ref=None,
        atlassian_auth_mode=None,
        atlassian_auth_mode_secret_ref=None,
        atlassian_cloud_id=None,
        atlassian_cloud_id_secret_ref=None,
        atlassian_email=None,
        atlassian_email_secret_ref=None,
        atlassian_service_account_email=None,
        atlassian_service_account_email_secret_ref=None,
        atlassian_site_url=None,
        atlassian_site_url_secret_ref=None,
        atlassian_username=None,
        atlassian_url=None,
        jira=JiraSettings(
            jira_tool_enabled=True,
            jira_allowed_projects=None,
            jira_allowed_actions=None,
        ),
    )


@pytest.mark.asyncio
async def test_jira_comment_blocks_before_send() -> None:
    raw = "synthetic-jira-809-secret"
    service = _StubJiraService(
        atlassian_settings=_jira_settings(), high_security_mode=True
    )
    with pytest.raises(JiraToolError) as exc_info:
        await service.add_comment(
            AddCommentRequest(issueKey="ENG-123", body=f"please post password={raw}")
        )
    assert service.calls == []
    assert raw not in str(exc_info.value)
    assert exc_info.value.code == "outbound_scan_blocked"

    clean = _StubJiraService(
        atlassian_settings=_jira_settings(), high_security_mode=True
    )
    await clean.add_comment(
        AddCommentRequest(issueKey="ENG-123", body="clean publication body")
    )
    assert len(clean.calls) == 1


@pytest.mark.asyncio
async def test_jules_send_message_blocks_before_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_HIGH_SECURITY_MODE", "true")
    raw = "synthetic-jules-809-secret"
    posted: list[dict] = []
    client = JulesClient(base_url="https://jules.example", api_key="k")

    async def _must_not_post(path, json):  # type: ignore[no-untyped-def]
        posted.append({"path": path, "json": json})
        return None

    monkeypatch.setattr(client, "_post_json_empty", _must_not_post)
    with pytest.raises(JulesClientError) as exc_info:
        await client.send_message(
            JulesSendMessageRequest(
                sessionId="sessions/abc", prompt=f"continue with password={raw}"
            )
        )
    assert posted == []
    assert raw not in str(exc_info.value)

    await client.aclose()


def test_native_allow_bound_to_digest_and_key() -> None:
    clean_body = {"message": {"text": "clean hello"}}
    evidence = scan_native_outbound(
        surface=NativeScanSurface.MESSAGE,
        body=clean_body,
        idempotency_key="idem-809-a",
        high_security_mode=True,
    )
    assert evidence.allowed is True
    assert evidence.payload_digest == canonical_payload_digest(clean_body)
    assert evidence.payload_digest == canonical_outbound_digest(clean_body)
    assert evidence.idempotency_key == "idem-809-a"

    mutated = {"message": {"text": "clean hello plus password=synthetic-809-x"}}
    assert canonical_payload_digest(mutated) != evidence.payload_digest
    with pytest.raises(NativeScanBlockedError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE,
            body=mutated,
            idempotency_key="idem-809-a",
            high_security_mode=True,
        )
    assert "synthetic-809-x" not in str(exc_info.value.evidence.audit_metadata())

    # Unknown top-level shape is enforcement-unavailable, never an allow.
    with pytest.raises(NativeScanEnforcementError):
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE,
            body=["not", "a", "mapping"],
            high_security_mode=True,
        )


def test_push_bundle_and_coverage_fail_closed() -> None:
    raw = "synthetic-push-809-secret"
    blocked = scan_outbound_bundle(
        [
            {"location": "git.push.commits:base..head", "content": "clean subject"},
            {"location": "git.push.diff:app.py", "content": f"api_key={raw}"},
        ],
        high_security_mode=True,
    )
    assert blocked.allowed is False
    assert raw not in str(blocked.model_dump())
    assert raw not in str(blocked.audit_metadata())

    reason = push_scan_coverage_error(
        commit_range="base..head",
        commit_metadata_len=5,
        max_commit_metadata_chars=100,
        changed_file_count=1,
        max_changed_files=200,
        oversized_diff_path="app/big.py",
    )
    assert reason is not None and "coverage incomplete" in reason

    assert (
        push_scan_coverage_error(
            commit_range="base..head",
            commit_metadata_len=5,
            max_commit_metadata_chars=100,
            changed_file_count=1,
            max_changed_files=200,
        )
        is None
    )


def test_operator_message_exact_content_and_redaction() -> None:
    raw = "synthetic-operator-809-secret"
    exact = f"operator note password={raw}"
    blocked = scan_outbound_text(
        exact, location="operator.send_message", high_security_mode=True
    )
    assert blocked.allowed is False
    assert raw not in "; ".join(blocked.sanitized_diagnostics)
    assert raw not in str(blocked.audit_metadata())

    # Changed-content retry gets a new digest: the earlier clean allow digest
    # must not equal the mutated payload digest.
    clean_digest = canonical_outbound_digest({"message": "clean hello"})
    mutated_digest = canonical_outbound_digest({"message": exact})
    assert clean_digest != mutated_digest

    clean = scan_outbound_text(
        "clean hello", location="operator.send_message", high_security_mode=True
    )
    assert clean.allowed is True
