"""Embedded evidence URLs cannot corrupt portable recovery records."""

import json
from dataclasses import replace

import pytest

from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.temporal.github_issue_attempts import (
    AttemptHandoff,
    build_retry_reset_handoff,
    parse_attempt_comment,
    reconstruct_from_comments,
    render_attempt_comment,
)
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401


def _handoff(report):
    return AttemptHandoff(
        attempt_id="att-4503-recovery-aaaa",
        deployment_id="inst-recovery",
        repository="example/repo",
        issue_number=1,
        activity="releasing",
        last_report=report,
        writers_stopped=True,
        outcome="failed",
        next_action="fresh_retry",
        remaining_requirements=("Retain source evidence",),
        verification_summary="Recorded recovery evidence remains available.",
        saved_branch="work/recovery",
        saved_sha="b" * 40,
        retry_allowance=3,
        retry_remaining=2,
    )


@pytest.mark.parametrize("leased", [False, True], ids=["historical-v1", "leased-v2"])
@pytest.mark.parametrize(
    "report,expected",
    [
        (
            (
                "Automatic recovery: controlling run stopped without new repository work; "
                "saved checkpoints: artifact://art_checkpoint"
            ),
            (
                "Automatic recovery: controlling run stopped without new repository work; "
                "saved checkpoints: artifact://art_checkpoint"
            ),
        ),
        (
            "Saved checkpoints: artifact://art_first, artifact://art_second",
            "Saved checkpoints: artifact://art_first, artifact://art_second",
        ),
        (
            'Saved "checkpoint" at artifact://art_checkpoint',
            'Saved "checkpoint" at artifact://art_checkpoint',
        ),
        (
            "Stopped safely.\nSaved checkpoint: artifact://art_checkpoint",
            "Stopped safely.\nSaved checkpoint: artifact://art_checkpoint",
        ),
        (
            "Saved file at file:///tmp/reproduction\\",
            "Saved file at file:///tmp/reproduction\\",
        ),
        (
            "Reproduce at https://example.test/check?token=canyon&findingId=123",
            "Reproduce at https://example.test/check?token=[REDACTED]&findingId=123",
        ),
    ],
    ids=[
        "recovery",
        "multiple",
        "escaped-quotes",
        "escaped-newline",
        "backslash",
        "query-secret",
    ],
)
def test_handoff_with_embedded_evidence_url_remains_recoverable(
    report, expected, leased
):
    handoff = _handoff(report)
    if leased:
        handoff = replace(
            handoff,
            lease_renewed_at="2026-11-08T12:00:00+00:00",
            lease_expires_at="2026-11-08T12:30:00+00:00",
        )

    rendered = render_attempt_comment(handoff)
    parsed = parse_attempt_comment(rendered)

    assert parsed.status == "ok", rendered
    assert parsed.handoff.to_dict() == replace(handoff, last_report=expected).to_dict()
    assert "canyon" not in rendered
    assert redact_sensitive_text(rendered) == rendered


@pytest.mark.parametrize(
    "authorized", [False, True], ids=["foreign-account", "operator"]
)
def test_embedded_url_preserves_reset_record_without_relaxing_poster_authority(
    authorized,
):
    previous = replace(
        _handoff("Recorded failure"), retry_allowance=1, retry_remaining=0
    )
    reset = build_retry_reset_handoff(
        attempt_id="att-4503-reset-bbbb",
        deployment_id="inst-recovery",
        repository=previous.repository,
        issue_number=previous.issue_number,
        authorized_by="moonmind-bot",
        authorized_at="2026-09-26T12:00:00+00:00",
        reason="Recover preserved progress at artifact://art_checkpoint",
        predecessor_attempt_id=previous.attempt_id,
        predecessor_comment_id="100",
        superseded_attempt_ids=[previous.attempt_id],
        allowance=1,
    )
    comments = [
        {
            "id": str(100 + index),
            "user": {"id": "9001", "login": "moonmind-bot"},
            "body": render_attempt_comment(handoff),
        }
        for index, handoff in enumerate((previous, reset))
    ]
    parsed = parse_attempt_comment(comments[-1]["body"])
    assert parsed.status == "ok", comments[-1]["body"]
    assert parsed.handoff.to_dict() == reset.to_dict()

    result = reconstruct_from_comments(
        comments,
        expected_repository=previous.repository,
        expected_issue_number=previous.issue_number,
        trusted_posters=["moonmind-bot"],
        reset_authorizer_id="9001" if authorized else "9002",
        max_attempts=1,
    )

    assert result.reason_code == ("allowed" if authorized else "budget_exhausted")
    assert result.retry_remaining == int(authorized)
    assert len(result.lineage) == 2
    assert result.to_dict()["retry"]["unauthorizedResets"] == int(not authorized)


def test_prose_url_does_not_hide_following_secret_fields_or_change_typed_values():
    original = {
        "description": "Saved checkpoint at artifact://art_checkpoint",
        "password": "https://example.test/canyon?extra=meadow",
        "count": 2,
        "ready": True,
        "resetAuthorization": "",
        "reproduction": "See https://example.test/?token=lantern&findingId=123",
    }
    source = json.dumps(original, separators=(",", ":"))

    safe = redact_sensitive_text(source)

    assert json.loads(safe) == {
        **original,
        "password": "[REDACTED]",
        "reproduction": "See https://example.test/?token=[REDACTED]&findingId=123",
    }
    assert all(secret not in safe for secret in ("canyon", "meadow", "lantern"))
    assert redact_sensitive_text(safe) == safe


@pytest.mark.parametrize("prefix", ["Operator's note: ", "Don't drop diagnostics: "])
def test_prose_apostrophe_does_not_expose_query_credential_suffix(prefix):
    source = prefix + "https://example.test/?token=canyon',tail&findingId=123"

    safe = redact_sensitive_text(source)

    assert safe == prefix + "https://example.test/?token=[REDACTED]&findingId=123"
    assert "canyon" not in safe and "tail" not in safe
    assert redact_sensitive_text(safe) == safe


@pytest.mark.parametrize(
    "prefix,expected_prefix",
    [
        ("Operator's note: ", "Operator's note: "),
        (
            "Visit https://example.test/?q=' then ",
            "Visit https://example.test/?q=' then ",
        ),
        (
            'Visit https://example.test/?q=" then ',
            'Visit https://example.test/?q=" then ',
        ),
        (
            "Visit https://example.test/?token='tail then ",
            "Visit https://example.test/?token=[REDACTED] then ",
        ),
    ],
    ids=["prose", "query-single-quote", "query-double-quote", "query-secret-quote"],
)
def test_unquoted_prose_or_url_quote_does_not_corrupt_following_machine_record(
    prefix, expected_prefix
):
    record = {
        "description": "Saved at artifact://art_checkpoint",
        "resetAuthorization": "",
        "password": "canyon",
    }
    source = prefix + json.dumps(record, separators=(",", ":"))

    safe = redact_sensitive_text(source)

    assert safe.startswith(expected_prefix)
    assert json.loads(safe[len(expected_prefix) :]) == {
        **record,
        "password": "[REDACTED]",
    }
    assert "canyon" not in safe
    assert redact_sensitive_text(safe) == safe


@pytest.mark.asyncio
@pytest.mark.usefixtures("deployment_without_recorded_connections")
@pytest.mark.parametrize("reject_release", [False, True])
async def test_finalizer_persists_embedded_checkpoint_before_confirming_release(
    journey,  # noqa: F811 -- imported pytest fixture
    reject_release,
):
    from moonmind.workflows.temporal import story_output_tools
    from moonmind.workflows.temporal.activities.github_issue_finalization_activities import (
        finalize_failed_attempt,
    )
    from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore

    state, service, sessions = journey
    owner = "default/embedded-checkpoint-recovery"
    brief = await story_output_tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": ""},
        {"execution_owner": owner},
        github_service_factory=lambda: service,
    )
    assert brief.status == "COMPLETED"
    store = IssueClaimStore(sessions)
    state["reject_release"] = reject_release
    reason = (
        "Automatic recovery: controlling run stopped without new repository work; "
        "saved checkpoints: artifact://art_checkpoint"
    )
    kwargs = {
        "repository": "example/repo",
        "issue_number": 3970,
        "execution_event": "failed",
        "writer_evidence": {
            "writersStopped": True,
            "stopMethod": "runtime_quiescence",
            "stopEvidence": "Controlling execution and descendants are closed",
        },
        "mutation_evidence": {
            "pushOutcome": "verified_absent",
            "prOutcome": "verified_absent",
            "mergeOutcome": "verified_absent",
        },
        "preservation_evidence": {
            "saveMethod": "explicit_no_work",
            "trustworthyNoWork": True,
        },
        "disposition_evidence": {
            "trustworthyNoWork": True,
            "freshRetryAllowed": True,
        },
        "reason": reason,
        "service": service,
    }

    if reject_release:
        with pytest.raises(ValueError, match="claim_update_failed"):
            await finalize_failed_attempt(
                **kwargs, claim_store=store, claim_receipt=await store.get(owner)
            )
    else:
        result = await finalize_failed_attempt(
            **kwargs, claim_store=store, claim_receipt=await store.get(owner)
        )
        assert result["released"]
    persisted = await IssueClaimStore(sessions).get(owner)
    parsed = parse_attempt_comment(persisted.comment_body)
    assert parsed.status == "ok", persisted.comment_body
    assert reason in parsed.handoff.last_report
    assert parsed.handoff.reset_authorization == ""
    assert persisted.released is not reject_release
    assert state["labels"] == []

    state["reject_release"] = False
    restarted_store = IssueClaimStore(sessions)
    repeated = await finalize_failed_attempt(
        **kwargs,
        claim_store=restarted_store,
        claim_receipt=await restarted_store.get(owner),
    )
    assert (await restarted_store.get(owner)).released, repeated
    assert state["labels"] == []
    assert state["posts"] == 1


@pytest.mark.parametrize(
    "prefix",
    [
        "PASSWORD=https://e.test/?q='tail ",
        'PASSWORD=https://e.test/?q="tail ',
        'PASSWORD="https://e.test/?q=\'tail" ',
        "PASSWORD=prefixhttps://e.test/?q='tail ",
        'PASSWORD="notes /tmp/path https://e.test/?q=\'tail" ',
    ],
    ids=["single", "double", "quoted", "embedded", "earlier-path"],
)
def test_consumed_url_valued_secret_cannot_open_following_record(prefix):
    record = '{"description":"Saved at artifact://art_checkpoint","resetAuthorization":"","password":"meadow"}'
    expected = 'PASSWORD=[REDACTED] {"description":"Saved at artifact://art_checkpoint","resetAuthorization":"","password":"[REDACTED]"}'
    assert redact_sensitive_text(prefix + record) == expected
    assert redact_sensitive_text(expected) == expected
