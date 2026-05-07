from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.task_proposals.delivery import (
    JiraProposalIssueProvider,
    ProposalDeliveryError,
    ProposalDeliveryRequest,
    ProposalDeliveryService,
    ProviderDecisionEvent,
    _safe_metadata,
    parse_provider_decision,
    render_github_issue,
    render_jira_issue,
)


def _request(
    *,
    provider: str = "github",
    repository: str = "Moon/Repo",
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
        category="tests",
        tags=("artifact_gap", "moonmind"),
        priority="high",
        dedup_key="moon/repo:add-regression-coverage",
        dedup_hash="d" * 64,
        task_snapshot_ref="artifact://task-snapshot.json",
        task_create_request={
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
    assert "moonmind-proposal" in rendered.labels
    assert "custom" in rendered.labels
    assert "<!-- moonmind-proposal" in rendered.body
    assert "wf-123" in rendered.body
    assert "artifact://task-snapshot.json" in rendered.body
    assert "/moonmind promote" in rendered.body
    assert "stored proposal snapshot" in rendered.body.lower()
    assert "RAW EXECUTABLE PAYLOAD SHOULD NOT APPEAR" not in rendered.body


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
    assert result.decision == "priority"
    assert result.priority == "urgent"
    assert "rm -rf" not in (result.note or "")


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
            action="priority",
            note="urgent",
        )
    )

    assert result.accepted is True
    assert result.decision == "priority"
    assert result.priority == "urgent"
    assert result.note == "urgent"


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
