from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest

from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.proposals.delivery import (
    GitHubProposalIssueProvider,
    JiraProposalIssueProvider,
    ProposalDecisionStateUpdate,
    ProposalDeliveryError,
    ProposalDeliveryRequest,
    ProposalDeliveryService,
    ProviderDecisionEvent,
    _decision_comment,
    _decision_state_labels,
    _safe_metadata,
    github_decision_event_from_payload,
    github_marker_for_proposal,
    parse_provider_decision,
    render_github_issue,
    render_jira_issue,
)


def _request(
    *,
    provider: str = "github",
    repository: str = "Moon/Repo",
    category: str | None = "tests",
    provider_metadata: dict[str, object] | None = None,
    resolved_policy: dict[str, object] | None = None,
    external_key: str | None = None,
    external_url: str | None = None,
) -> ProposalDeliveryRequest:
    return ProposalDeliveryRequest(
        record_id=str(uuid4()),
        provider=provider,
        repository=repository,
        title="Add regression coverage",
        summary="Follow-up proposal from workflow evidence.",
        category=category,
        tags=("artifact_gap", "moonmind"),
        priority="high",
        dedup_key="moon/repo:add-regression-coverage",
        dedup_hash="d" * 64,
        workflow_snapshot_ref="artifact://task-snapshot.json",
        workflow_create_request={
            "payload": {
                "repository": repository,
                "task": {"instructions": "RAW EXECUTABLE PAYLOAD SHOULD NOT APPEAR"},
            }
        },
        origin_metadata={
            "workflow_id": "wf-123",
            "temporal_run_id": "run-456",
            "trigger_repo": repository,
            "trigger_job_id": "job-789",
        },
        provider_metadata=provider_metadata or {},
        resolved_policy=resolved_policy or {},
        external_key=external_key,
        external_url=external_url,
    )


@dataclass
class FakeProvider:
    existing: dict[str, object] | None = None
    created_payload: dict[str, object] | None = None
    updated_payload: dict[str, object] | None = None

    def __post_init__(self) -> None:
        self.searches: list[ProposalDeliveryRequest] = []
        self.creates: list[object] = []
        self.updates: list[tuple[object, dict[str, object]]] = []
        self.decision_states: list[dict[str, object]] = []

    async def apply_decision_state(
        self,
        request: ProposalDeliveryRequest,
        issue: dict[str, object],
        labels,
        comment,
    ) -> dict[str, object]:
        self.decision_states.append(
            {
                "issue": issue,
                "labels": list(labels),
                "comment": comment,
            }
        )
        return {"labelsUpdated": True, "commented": bool(comment)}

    async def search_issue(self, request: ProposalDeliveryRequest) -> dict[str, object] | None:
        self.searches.append(request)
        return self.existing

    async def create_issue(
        self, request: ProposalDeliveryRequest, rendered: object
    ) -> dict[str, object]:
        self.creates.append(rendered)
        return self.created_payload or {
            "external_key": "42",
            "external_url": "https://github.example/Moon/Repo/issues/42",
        }

    async def update_issue(
        self,
        request: ProposalDeliveryRequest,
        rendered: object,
        issue: dict[str, object],
    ) -> dict[str, object]:
        self.updates.append((rendered, issue))
        return self.updated_payload or {
            "external_key": str(issue.get("key") or "42"),
            "external_url": str(issue.get("url") or "https://github.example/Moon/Repo/issues/42"),
        }


def test_github_renderer_includes_review_context_and_excludes_raw_payload() -> None:
    rendered = render_github_issue(
        _request(provider_metadata={"github": {"labels": ["custom"]}}),
        redactor=SecretRedactor(["ghp_secret"], "[REDACTED]"),
    )

    assert rendered.title.startswith("[MoonMind proposal] Add regression coverage")
    assert "moonmind:proposal" in rendered.labels
    assert "moonmind:state:open" in rendered.labels
    assert "moonmind:target:workflow-repo" in rendered.labels
    assert "moonmind:category:tests" in rendered.labels
    assert "moonmind:priority:high" in rendered.labels
    assert "moonmind:dedup:dddddddddddd" in rendered.labels
    assert "custom" in rendered.labels
    assert "<!-- moonmind-proposal" in rendered.body
    assert "target=workflow-repo" in rendered.body
    assert rendered.marker.index("record=") < rendered.marker.index("dedup=")
    assert rendered.marker.index("dedup=") < rendered.marker.index("snapshot=")
    assert rendered.marker.index("snapshot=") < rendered.marker.index("target=")
    assert "wf-123" in rendered.body
    assert "artifact://task-snapshot.json" in rendered.body
    assert "/moonmind promote" in rendered.body
    assert "stored proposal snapshot" in rendered.body.lower()
    assert "RAW EXECUTABLE PAYLOAD SHOULD NOT APPEAR" not in rendered.body


def test_github_renderer_labels_moonmind_target_from_policy() -> None:
    rendered = render_github_issue(
        _request(
            category="run_quality",
            resolved_policy={"target": "moonmind"},
        )
    )

    assert "moonmind:target:moonmind" in rendered.labels
    assert "moonmind:category:run-quality" in rendered.labels
    assert "target=moonmind" in rendered.body


def test_github_renderer_caps_long_category_labels() -> None:
    rendered = render_github_issue(
        _request(category="A category value that is much longer than github allows")
    )

    category_labels = [
        label for label in rendered.labels if label.startswith("moonmind:category:")
    ]
    assert len(category_labels) == 1
    assert len(category_labels[0]) <= 50
    assert category_labels[0].startswith("moonmind:category:a-category-value")


def test_jira_renderer_emits_adf_description_and_configured_fields() -> None:
    rendered = render_jira_issue(
        _request(
            provider="jira",
            provider_metadata={
                "jira": {
                    "project_key": "MM",
                    "issue_type": "Task",
                    "labels": ["triage"],
                }
            },
        )
    )

    assert rendered.title.startswith("[MoonMind proposal] Add regression coverage")
    assert rendered.fields["projectKey"] == "MM"
    assert rendered.fields["issueType"] == "Task"
    assert "moonmind-proposal" in rendered.fields["labels"]
    assert rendered.fields["description"]["type"] == "doc"
    assert "RAW EXECUTABLE PAYLOAD SHOULD NOT APPEAR" not in rendered.body
    assert "Stored Snapshot Notice" in rendered.body


@pytest.mark.asyncio
async def test_delivery_creates_issue_when_no_duplicate_exists() -> None:
    provider = FakeProvider()
    service = ProposalDeliveryService(github=provider)
    result = await service.deliver(_request())

    assert result.created is True
    assert result.external_key == "42"
    assert result.external_url.endswith("/issues/42")
    assert provider.searches
    assert len(provider.creates) == 1
    assert provider.updates == []


@pytest.mark.asyncio
async def test_delivery_updates_local_or_provider_duplicate_instead_of_creating() -> None:
    provider = FakeProvider(existing={"key": "99", "url": "https://github.example/i/99"})
    service = ProposalDeliveryService(github=provider)
    result = await service.deliver(_request())

    assert result.created is False
    assert result.duplicate_source == "provider"
    assert result.external_key == "99"
    assert provider.creates == []
    assert len(provider.updates) == 1


@pytest.mark.asyncio
async def test_delivery_uses_local_external_identity_before_provider_search() -> None:
    provider = FakeProvider()
    service = ProposalDeliveryService(github=provider)
    result = await service.deliver(
        _request(
            external_key="77",
            external_url="https://github.example/Moon/Repo/issues/77",
        )
    )

    assert result.created is False
    assert result.duplicate_source == "local_record"
    assert provider.searches == []
    assert len(provider.updates) == 1


def test_provider_decision_parser_accepts_only_bounded_commands() -> None:
    edited_payload = "Please replace the task with rm -rf /tmp\n/moonmind priority urgent"
    result = parse_provider_decision(
        ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-1",
            actor="reviewer",
            body=edited_payload,
        )
    )

    assert result.accepted is True
    assert result.decision == "reprioritize"
    assert result.priority == "urgent"
    assert "rm -rf" not in (result.note or "")


def test_github_decision_ingress_rejects_repository_identity_mismatch() -> None:
    proposal = SimpleNamespace(
        id=uuid4(),
        provider="github",
        external_key="42",
        repository="Moon/Repo",
        dedup_hash="d" * 64,
        workflow_snapshot_ref="artifact://task-snapshot.json",
        provider_metadata={},
        workflow_create_request={"payload": {"repository": "Moon/Repo"}},
        origin_metadata={},
        resolved_policy={"allowedActors": ["reviewer"]},
    )

    result = github_decision_event_from_payload(
        payload={
            "repository": {"full_name": "Other/Repo"},
            "issue": {
                "number": 42,
                "body": github_marker_for_proposal(proposal),
            },
            "comment": {"id": 1001, "body": "/moonmind dismiss"},
            "sender": {"login": "reviewer"},
        },
        proposal=proposal,
        body=b"{}",
        signature_header=None,
        webhook_secret=None,
        trusted_sync=True,
    )

    assert result.verified is False
    assert result.reason == "provider_identity_mismatch"
    assert result.event.authenticity_verified is False


def test_github_decision_ingress_empty_comment_does_not_fall_back_to_issue_body() -> None:
    proposal = SimpleNamespace(
        id=uuid4(),
        provider="github",
        external_key="42",
        repository="Moon/Repo",
        dedup_hash="d" * 64,
        workflow_snapshot_ref="artifact://task-snapshot.json",
        provider_metadata={},
        workflow_create_request={"payload": {"repository": "Moon/Repo"}},
        origin_metadata={},
        resolved_policy={"allowedActors": ["reviewer"]},
    )

    result = github_decision_event_from_payload(
        payload={
            "repository": {"full_name": "Moon/Repo"},
            "issue": {
                "number": 42,
                "body": (
                    f"{github_marker_for_proposal(proposal)}\n"
                    "/moonmind promote"
                ),
            },
            "comment": {"id": 1001, "body": ""},
            "sender": {"login": "reviewer"},
        },
        proposal=proposal,
        body=b"{}",
        signature_header=None,
        webhook_secret=None,
        trusted_sync=True,
    )

    assert result.verified is True
    assert result.event.body == ""


def test_provider_decision_parser_accepts_request_revision_command() -> None:
    result = parse_provider_decision(
        ProviderDecisionEvent(
            provider="jira",
            external_key="MM-1",
            provider_event_id="evt-revision",
            actor="reviewer",
            body="/moonmind request-revision Need a smaller task split",
        )
    )

    assert result.accepted is True
    assert result.decision == "request_revision"
    assert result.note == "Need a smaller task split"


def test_provider_decision_parser_rejects_blank_provider_event_id() -> None:
    result = parse_provider_decision(
        ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id=" ",
            actor="reviewer",
            body="/moonmind promote",
        )
    )

    assert result.accepted is False
    assert result.reason == "missing_provider_event_id"


def test_provider_decision_parser_rejects_unknown_commands() -> None:
    result = parse_provider_decision(
        ProviderDecisionEvent(
            provider="jira",
            external_key="MM-1",
            provider_event_id="evt-2",
            actor="reviewer",
            body="/moonmind replace-task do something else",
        )
    )

    assert result.accepted is False
    assert result.reason == "unsupported_action"


def test_provider_decision_parser_accepts_structured_priority_note() -> None:
    result = parse_provider_decision(
        ProviderDecisionEvent(
            provider="jira",
            external_key="MM-1",
            provider_event_id="evt-3",
            actor="reviewer",
            body="",
            action="reprioritize",
            note="urgent",
        )
    )

    assert result.accepted is True
    assert result.decision == "reprioritize"
    assert result.priority == "urgent"
    assert result.note == "urgent"


def test_provider_decision_parser_extracts_bounded_runtime_control() -> None:
    result = parse_provider_decision(
        ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-runtime",
            actor="reviewer",
            body="/moonmind promote --runtime codex Ready to run",
        )
    )

    assert result.accepted is True
    assert result.decision == "promote"
    assert result.runtime_mode == "codex"
    assert result.note == "Ready to run"


@pytest.mark.asyncio
async def test_delivery_blocks_disallowed_github_repository() -> None:
    service = ProposalDeliveryService(github=FakeProvider())

    with pytest.raises(ProposalDeliveryError) as exc:
        await service.deliver(
            _request(resolved_policy={"allowedRepositories": ["Other/Repo"]})
        )

    assert "not allowed" in str(exc.value)
    assert exc.value.to_output()["sanitizedReason"] == str(exc.value)


@pytest.mark.asyncio
async def test_delivery_policy_allows_restricted_supported_actions() -> None:
    provider = FakeProvider()
    service = ProposalDeliveryService(github=provider)

    result = await service.deliver(_request(resolved_policy={"allowedActions": ["dismiss"]}))

    assert result.created is True
    assert len(provider.creates) == 1


@pytest.mark.asyncio
async def test_delivery_policy_rejects_unknown_allowed_action() -> None:
    service = ProposalDeliveryService(github=FakeProvider())

    with pytest.raises(ProposalDeliveryError) as exc:
        await service.deliver(
            _request(resolved_policy={"allowedActions": ["dismiss", "replace-task"]})
        )

    assert "reviewer actions are not allowed" in str(exc.value)


@pytest.mark.asyncio
async def test_delivery_blocks_secret_like_provider_metadata() -> None:
    service = ProposalDeliveryService(github=FakeProvider())

    with pytest.raises(ProposalDeliveryError) as exc:
        await service.deliver(
            _request(provider_metadata={"github": {"token": "ghp_secret"}})
        )

    assert "secret-like" in str(exc.value)
    assert "ghp_secret" not in str(exc.value.to_output())


@pytest.mark.asyncio
async def test_delivery_requires_provider_identity_from_adapter() -> None:
    service = ProposalDeliveryService(github=FakeProvider(created_payload={"created": True}))

    with pytest.raises(ProposalDeliveryError) as exc:
        await service.deliver(_request())

    assert exc.value.retryable is True
    assert "external issue identity" in str(exc.value)


def test_safe_metadata_redacts_secret_keys_inside_nested_lists() -> None:
    safe = _safe_metadata(
        {
            "items": [
                ["plain", {"token": "ghp_secret"}],
                {"nested": [{"private_key": "secret-value"}]},
            ]
        }
    )

    assert safe == {
        "items": [
            ["plain", {"token": "[REDACTED]"}],
            {"nested": [{"private_key": "[REDACTED]"}]},
        ]
    }


@pytest.mark.asyncio
async def test_jira_provider_does_not_use_issue_key_as_external_url() -> None:
    class FakeJira:
        async def create_issue(self, request):
            return {"issueKey": "MM-1"}

        async def edit_issue(self, request):
            return None

    provider = JiraProposalIssueProvider(FakeJira())
    rendered = render_jira_issue(_request(provider="jira", repository="MM"))

    created = await provider.create_issue(_request(provider="jira", repository="MM"), rendered)
    updated = await provider.update_issue(
        _request(provider="jira", repository="MM"),
        rendered,
        {"key": "MM-1"},
    )

    assert created == {"external_key": "MM-1", "external_url": None}
    assert updated == {"external_key": "MM-1", "external_url": None}


# ---------------------------------------------------------------------------
# MM-858: provider-visible promotion/decision state updates
# ---------------------------------------------------------------------------

def test_decision_state_labels_transition_github_state_and_priority() -> None:
    labels = _decision_state_labels(
        _request(),
        resulting_state="promoted",
        priority="urgent",
    )

    assert "moonmind:state:promoted" in labels
    assert "moonmind:state:open" not in labels
    assert "moonmind:priority:urgent" in labels
    # Canonical proposal/target/category labels are preserved.
    assert "moonmind:proposal" in labels
    assert "moonmind:target:workflow-repo" in labels


def test_decision_state_labels_transition_jira_state() -> None:
    labels = _decision_state_labels(
        _request(provider="jira", repository="MM"),
        resulting_state="dismissed",
    )

    assert "moonmind-proposal" in labels
    assert "moonmind-state-dismissed" in labels


def test_decision_comment_for_promotion_includes_execution_link() -> None:
    comment = _decision_comment(
        ProposalDecisionStateUpdate(
            decision="promote",
            accepted=True,
            actor="reviewer",
            provider_event_id="evt-promote",
            resulting_state="promoted",
            promoted_execution_id="wf-promoted-1",
            promoted_execution_url="/workflows/wf-promoted-1?source=temporal",
        ),
        redactor=SecretRedactor([], "[REDACTED]"),
    )

    assert "wf-promoted-1" in comment
    assert "/workflows/wf-promoted-1?source=temporal" in comment
    assert "audit trail" in comment.lower()


@pytest.mark.asyncio
async def test_record_decision_pushes_promoted_state_and_execution_link() -> None:
    provider = FakeProvider()
    service = ProposalDeliveryService(
        github=provider, redactor=SecretRedactor([], "[REDACTED]")
    )
    request = _request(
        external_key="42",
        external_url="https://github.example/Moon/Repo/issues/42",
    )

    result = await service.record_decision(
        request,
        ProposalDecisionStateUpdate(
            decision="promote",
            accepted=True,
            actor="reviewer",
            provider_event_id="evt-promote",
            resulting_state="promoted",
            promoted_execution_id="wf-promoted-1",
            promoted_execution_url="/workflows/wf-promoted-1?source=temporal",
        ),
    )

    assert result["applied"] is True
    assert result["resultingExternalState"] == "promoted"
    assert result["promotedExecutionId"] == "wf-promoted-1"
    assert len(provider.decision_states) == 1
    pushed = provider.decision_states[0]
    assert "moonmind:state:promoted" in pushed["labels"]
    assert pushed["issue"]["key"] == "42"
    assert "wf-promoted-1" in pushed["comment"]
    # Original issue body/title are never rewritten by a state transition.
    assert provider.updates == []
    assert provider.creates == []


@pytest.mark.asyncio
async def test_record_decision_skips_when_no_external_issue() -> None:
    provider = FakeProvider()
    service = ProposalDeliveryService(github=provider)

    result = await service.record_decision(
        _request(),
        ProposalDecisionStateUpdate(
            decision="dismiss",
            accepted=True,
            actor="reviewer",
            provider_event_id="evt-dismiss",
            resulting_state="dismissed",
        ),
    )

    assert result["applied"] is False
    assert result["reason"] == "missing_external_issue"
    assert provider.decision_states == []


@pytest.mark.asyncio
async def test_record_decision_reports_unapplied_when_provider_update_fails() -> None:
    """A non-raising provider failure (e.g. missing token) must not record applied."""

    @dataclass
    class FailingProvider(FakeProvider):
        async def apply_decision_state(self, request, issue, labels, comment):
            self.decision_states.append(
                {"issue": issue, "labels": list(labels), "comment": comment}
            )
            # GitHubService returns updated=False/created=False instead of raising
            # when the token is missing or GitHub returns an error.
            return {"labelsUpdated": False, "commented": False}

    provider = FailingProvider()
    service = ProposalDeliveryService(
        github=provider, redactor=SecretRedactor([], "[REDACTED]")
    )

    result = await service.record_decision(
        _request(
            external_key="42",
            external_url="https://github.example/Moon/Repo/issues/42",
        ),
        ProposalDecisionStateUpdate(
            decision="promote",
            accepted=True,
            actor="reviewer",
            provider_event_id="evt-promote",
            resulting_state="promoted",
            promoted_execution_id="wf-promoted-1",
            promoted_execution_url="/workflows/wf-promoted-1?source=temporal",
        ),
    )

    assert result["applied"] is False
    assert result["reason"] == "provider_state_update_failed"
    assert result["commented"] is False
    assert result["providerResponse"]["labelsUpdated"] is False
    assert len(provider.decision_states) == 1


@pytest.mark.asyncio
async def test_github_apply_decision_state_updates_labels_and_comments_without_body() -> None:
    class FakeGitHub:
        def __init__(self) -> None:
            self.label_calls: list[dict[str, object]] = []
            self.comment_calls: list[dict[str, object]] = []
            self.update_calls: list[dict[str, object]] = []

        async def set_issue_labels(self, *, repo, issue_number, labels):
            self.label_calls.append(
                {"repo": repo, "issue_number": issue_number, "labels": list(labels)}
            )
            return SimpleNamespace(updated=True, external_url=None)

        async def comment_on_issue(self, *, repo, issue_number, body):
            self.comment_calls.append(
                {"repo": repo, "issue_number": issue_number, "body": body}
            )
            return SimpleNamespace(
                created=True,
                external_url="https://github.example/Moon/Repo/issues/42#c1",
            )

        async def update_issue(self, **kwargs):  # pragma: no cover - guard
            self.update_calls.append(kwargs)
            return SimpleNamespace()

    github = FakeGitHub()
    provider = GitHubProposalIssueProvider(github)

    applied = await provider.apply_decision_state(
        _request(external_key="42"),
        {"key": "42", "url": "https://github.example/Moon/Repo/issues/42"},
        ["moonmind:proposal", "moonmind:state:promoted"],
        "Execution link: /workflows/wf-1?source=temporal",
    )

    assert applied["labelsUpdated"] is True
    assert applied["commented"] is True
    assert github.label_calls[0]["labels"] == [
        "moonmind:proposal",
        "moonmind:state:promoted",
    ]
    assert github.comment_calls[0]["issue_number"] == "42"
    # The issue body is never rewritten via the state-update path.
    assert github.update_calls == []


@pytest.mark.asyncio
async def test_jira_apply_decision_state_sets_labels_and_comments() -> None:
    class FakeJira:
        def __init__(self) -> None:
            self.edits: list[object] = []
            self.comments: list[object] = []

        async def edit_issue(self, request):
            self.edits.append(request)
            return {"updated": True}

        async def add_comment(self, request):
            self.comments.append(request)
            return {"commented": True}

    jira = FakeJira()
    provider = JiraProposalIssueProvider(jira)

    applied = await provider.apply_decision_state(
        _request(provider="jira", repository="MM", external_key="MM-1"),
        {"key": "MM-1"},
        ["moonmind-proposal", "moonmind-state-promoted"],
        "Execution link: /workflows/wf-1?source=temporal",
    )

    assert applied["labelsUpdated"] is True
    assert applied["commented"] is True
    assert jira.edits[0].fields == {
        "labels": ["moonmind-proposal", "moonmind-state-promoted"]
    }
    assert jira.comments[0].issue_key == "MM-1"


def test_provider_decision_parser_ignores_replacement_repository_env_and_tools() -> None:
    """Only bounded runtime control is honored; injected executable overrides
    (repository, environment variables, credentials, tool config) are ignored."""
    result = parse_provider_decision(
        ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-inject",
            actor="reviewer",
            body=(
                "/moonmind promote --runtime codex --repository Evil/Repo "
                "--env SECRET=leak --credential ghp_inject --tool shell:rm-rf"
            ),
        )
    )

    assert result.accepted is True
    assert result.decision == "promote"
    # The only bounded executable control extracted from issue text is runtime.
    assert result.runtime_mode == "codex"
    # ProviderDecisionResult carries no replacement repository/env/credential/tool
    # fields, so injected executable overrides cannot reach promotion.
    for forbidden in ("repository", "environment", "env", "credential", "tool"):
        assert not hasattr(result, forbidden)
