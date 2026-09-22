"""Hermetic tests for the opt-in GitHub event trigger path (#3967).

Covers the thin first slice only: one opted-in ``issues/labeled`` event
resolves an authorized existing preset; the exact bounded raw body is
verified with X-Hub-Signature-256 before any field is trusted; one durable
receipt owner deduplicates redeliveries (changed-body duplicates conflict);
stale/self/unsupported/fork/revoked deliveries never launch.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from moonmind.workflows.adapters.github_event_delivery import (
    MAX_WEBHOOK_BODY_BYTES,
    RECEIPT_RETENTION_SECONDS,
    EventTriggerConfig,
    IncomingDelivery,
    StoredReceipt,
    classify_delivery,
    decide_delivery,
    delivery_from_webhook_payload,
    delivery_key,
    is_receipt_expired,
    load_trigger_configs,
    payload_digest,
    resolve_trigger,
    sign_webhook_body,
    trigger_from_mapping,
    verify_webhook_signature,
)

_SECRET = b"test-webhook-secret-0123456789"


def _config(**overrides) -> EventTriggerConfig:
    base = {
        "name": "label-triage",
        "repository": "acme/repo",
        "installation_id": "12345",
        "event_name": "issues",
        "action": "labeled",
        "permitted_actors": ("alice",),
        "label": "mm-ready",
        "preset_slug": "triage-preset",
        "enabled": True,
    }
    base.update(overrides)
    return trigger_from_mapping(base)


def _delivery(**overrides) -> IncomingDelivery:
    base = {
        "delivery_id": "del-1",
        "event_name": "issues",
        "action": "labeled",
        "installation_id": "12345",
        "repository": "acme/repo",
        "actor": "alice",
        "actor_type": "User",
        "label": "mm-ready",
        "issue_number": 7,
        "received_at_epoch": time.time(),
        "is_fork_content": False,
        "sender_is_app_bot": False,
    }
    base.update(overrides)
    return IncomingDelivery(**base)


# ---------------------------------------------------------------------------
# R1: opt-in trigger config
# ---------------------------------------------------------------------------


def test_trigger_config_normalizes_repository_case():
    config = _config(repository="Acme/Repo")
    assert config.repository == "acme/repo"


def test_load_trigger_configs_empty_by_default():
    assert load_trigger_configs([]) == ()
    assert load_trigger_configs(None) == ()


def test_trigger_from_mapping_rejects_missing_preset():
    with pytest.raises(ValueError):
        _config(preset_slug="")


def test_trigger_from_mapping_rejects_labeled_without_label():
    with pytest.raises(ValueError):
        _config(label="")


def test_resolve_trigger_matches_exact_binding():
    configs = (_config(), _config(name="other", repository="acme/other"))
    matched = resolve_trigger(
        configs,
        repository="acme/repo",
        installation_id="12345",
        event_name="issues",
        action="labeled",
        actor="alice",
        label="mm-ready",
    )
    assert matched is not None
    assert matched.name == "label-triage"


def test_resolve_trigger_rejects_wrong_actor_repo_label_installation():
    configs = (_config(),)
    for kwargs in (
        {"actor": "mallory"},
        {"repository": "acme/other"},
        {"installation_id": "99999"},
        {"label": "other-label"},
        {"action": "unlabeled"},
        {"event_name": "issue_comment"},
    ):
        base = {
            "repository": "acme/repo",
            "installation_id": "12345",
            "event_name": "issues",
            "action": "labeled",
            "actor": "alice",
            "label": "mm-ready",
        }
        base.update(kwargs)
        assert resolve_trigger(configs, **base) is None


def test_resolve_trigger_skips_disabled_config():
    configs = (_config(enabled=False),)
    assert (
        resolve_trigger(
            configs,
            repository="acme/repo",
            installation_id="12345",
            event_name="issues",
            action="labeled",
            actor="alice",
            label="mm-ready",
        )
        is None
    )


def test_resolve_trigger_actor_match_is_case_insensitive():
    configs = (_config(),)
    matched = resolve_trigger(
        configs,
        repository="acme/repo",
        installation_id="12345",
        event_name="issues",
        action="labeled",
        actor="Alice",
        label="mm-ready",
    )
    assert matched is not None


# ---------------------------------------------------------------------------
# R2: signature verification over the exact bounded raw body
# ---------------------------------------------------------------------------


def test_sign_and_verify_round_trip():
    raw = b'{"action":"labeled"}'
    header = sign_webhook_body(raw, _SECRET)
    assert header.startswith("sha256=")
    ok, reason = verify_webhook_signature(
        raw_body=raw, signature_header=header, secret=_SECRET
    )
    assert (ok, reason) == (True, "signature_valid")


def test_verify_rejects_tampered_body():
    raw = b'{"action":"labeled"}'
    header = sign_webhook_body(raw, _SECRET)
    ok, reason = verify_webhook_signature(
        raw_body=b'{"action":"unlabeled"}',
        signature_header=header,
        secret=_SECRET,
    )
    assert ok is False
    assert reason == "signature_mismatch"


def test_verify_rejects_wrong_secret_malformed_and_empty():
    raw = b'{"action":"labeled"}'
    header = sign_webhook_body(raw, _SECRET)
    ok, _ = verify_webhook_signature(
        raw_body=raw, signature_header=header, secret=b"wrong-secret"
    )
    assert ok is False
    for bad in ("", "md5=abc", "sha256=zzzz", "sha256="):
        ok, reason = verify_webhook_signature(
            raw_body=raw, signature_header=bad, secret=_SECRET
        )
        assert ok is False, bad
        assert reason in {
            "signature_missing",
            "signature_malformed",
            "signature_mismatch",
        }


def test_verify_rejects_oversized_body_without_comparing():
    raw = b"x" * (MAX_WEBHOOK_BODY_BYTES + 1)
    header = sign_webhook_body(raw, _SECRET)
    ok, reason = verify_webhook_signature(
        raw_body=raw, signature_header=header, secret=_SECRET
    )
    assert (ok, reason) == (False, "body_too_large")


def test_verify_uses_constant_time_compare():
    raw = b"{}"
    header = sign_webhook_body(raw, _SECRET)
    expected = hmac.new(_SECRET, raw, hashlib.sha256).hexdigest()
    assert header == f"sha256={expected}"


def test_payload_digest_is_stable_sha256():
    assert payload_digest(b"abc") == hashlib.sha256(b"abc").hexdigest()


# ---------------------------------------------------------------------------
# R2/R4: delivery validation gates
# ---------------------------------------------------------------------------


def test_delivery_from_webhook_payload_coerces_labeled_issue():
    payload = {
        "action": "labeled",
        "installation": {"id": 12345},
        "repository": {"full_name": "acme/repo"},
        "sender": {"login": "alice", "type": "User"},
        "label": {"name": "mm-ready"},
        "issue": {"number": 7},
    }
    delivery = delivery_from_webhook_payload(
        delivery_id="del-1",
        event_name="issues",
        payload=payload,
        received_at_epoch=1700000000.0,
    )
    assert delivery.repository == "acme/repo"
    assert delivery.actor == "alice"
    assert delivery.label == "mm-ready"
    assert delivery.issue_number == 7
    assert delivery.installation_id == "12345"


def test_delivery_from_webhook_payload_rejects_malformed():
    with pytest.raises(ValueError):
        delivery_from_webhook_payload(
            delivery_id="",
            event_name="issues",
            payload={},
            received_at_epoch=0.0,
        )
    with pytest.raises(ValueError):
        delivery_from_webhook_payload(
            delivery_id="del-1",
            event_name="issues",
            payload="not-a-mapping",
            received_at_epoch=0.0,
        )


def _decide(delivery, configs, digest="digest-1", stored=None, now=None):
    return decide_delivery(
        delivery=delivery,
        configs=configs,
        payload_digest_hex=digest,
        now_epoch=now if now is not None else delivery.received_at_epoch + 1.0,
        stored=stored,
    )


def test_decide_admits_signed_opted_in_delivery():
    decision = _decide(_delivery(), (_config(),))
    assert decision.outcome == "admitted"
    assert decision.preset_slug == "triage-preset"
    assert decision.identity_key.startswith("github-event:v1:")
    assert decision.reason_code == "admitted"


def test_identity_key_is_fixed_length_deterministic_and_scoped():
    from moonmind.workflows.adapters.github_event_delivery import _identity_key

    first = _identity_key(
        installation_id="12345",
        repository="acme/repo",
        delivery_id="del-1",
        digest_hex="digest-1",
    )
    assert first.startswith("github-event:v1:")
    assert len(first) <= 128
    assert first == _identity_key(
        installation_id="12345",
        repository="acme/repo",
        delivery_id="del-1",
        digest_hex="digest-1",
    )
    assert first != _identity_key(
        installation_id="12345",
        repository="acme/repo",
        delivery_id="del-1",
        digest_hex="digest-2",
    )
    # A very long repository name still fits the 128-char idempotency column.
    long_repo = "a" * 60 + "/" + "b" * 60
    long_key = _identity_key(
        installation_id="123456789",
        repository=long_repo,
        delivery_id="12345678-1234-1234-1234-123456789012",
        digest_hex="d" * 64,
    )
    assert len(long_key) <= 128


def test_decide_ignores_unsupported_events_safely():
    decision = _decide(_delivery(event_name="push", action=""), (_config(),))
    assert decision.outcome == "ignored"
    assert decision.reason_code == "unsupported_event"
    assert decision.preset_slug == ""


def test_decide_ignores_self_generated_bot_events():
    decision = _decide(
        _delivery(actor="moonmind-bot[bot]", actor_type="Bot", sender_is_app_bot=True),
        (_config(),),
    )
    assert decision.outcome == "ignored"
    assert decision.reason_code == "self_event"


def test_decide_rejects_stale_events_without_launch():
    old = _delivery(received_at_epoch=1700000000.0)
    decision = _decide(old, (_config(),), now=1700000000.0 + 3600.0)
    assert decision.outcome == "rejected"
    assert decision.reason_code == "stale_event"


def test_decide_rejects_fork_content_by_default():
    decision = _decide(_delivery(is_fork_content=True), (_config(),))
    assert decision.outcome == "rejected"
    assert decision.reason_code == "fork_content_untrusted"


def test_decide_rejects_unknown_repo_and_unauthorized_actor():
    decision = _decide(_delivery(repository="acme/other"), (_config(),))
    assert (decision.outcome, decision.reason_code) == (
        "rejected",
        "no_matching_trigger",
    )
    decision = _decide(_delivery(actor="mallory"), (_config(),))
    assert (decision.outcome, decision.reason_code) == (
        "rejected",
        "no_matching_trigger",
    )


def test_decide_rejects_disabled_trigger_as_revoked():
    config = _config(enabled=False)
    # Resolution skips disabled configs, so the delivery has no trigger.
    assert (
        resolve_trigger(
            (config,),
            repository="acme/repo",
            installation_id="12345",
            event_name="issues",
            action="labeled",
            actor="alice",
            label="mm-ready",
        )
        is None
    )


# ---------------------------------------------------------------------------
# R3: durable receipt dedup owner
# ---------------------------------------------------------------------------


def test_delivery_key_scopes_installation_repo_delivery():
    assert (
        delivery_key(
            installation_id="12345", repository="acme/repo", delivery_id="del-1"
        )
        == "github-delivery:v1:12345:acme/repo:del-1"
    )


def test_classify_delivery_matrix():
    assert (
        classify_delivery(
            stored_digest="aaa", incoming_digest="aaa", same_delivery=True
        )
        == "redelivery_reuse"
    )
    assert (
        classify_delivery(
            stored_digest="aaa", incoming_digest="bbb", same_delivery=True
        )
        == "conflict_changed_body"
    )
    assert (
        classify_delivery(
            stored_digest="aaa", incoming_digest="aaa", same_delivery=False
        )
        == "new_request"
    )


def test_decide_redelivery_reuses_stored_execution_ref():
    stored = StoredReceipt(
        delivery_key="github-delivery:v1:12345:acme/repo:del-1",
        payload_digest="digest-1",
        decision="admitted_dispatched",
        execution_ref="temporal:exec-1",
        received_at_epoch=1700000000.0,
    )
    decision = _decide(
        _delivery(received_at_epoch=1700000000.0),
        (_config(),),
        digest="digest-1",
        stored=stored,
        now=1700000000.0 + 1.0,
    )
    assert decision.outcome == "redelivery_reuse"
    assert decision.execution_ref == "temporal:exec-1"


def test_decide_changed_body_conflicts_instead_of_relaunching():
    stored = StoredReceipt(
        delivery_key="github-delivery:v1:12345:acme/repo:del-1",
        payload_digest="digest-1",
        decision="admitted_dispatched",
        execution_ref="temporal:exec-1",
        received_at_epoch=1700000000.0,
    )
    decision = _decide(
        _delivery(received_at_epoch=1700000000.0),
        (_config(),),
        digest="digest-2",
        stored=stored,
        now=1700000000.0 + 1.0,
    )
    assert decision.outcome == "conflict"
    assert decision.reason_code == "changed_body_conflict"
    assert decision.execution_ref == ""


def test_failed_dispatch_allows_bounded_redelivery_reattempt():
    stored = StoredReceipt(
        delivery_key="github-delivery:v1:12345:acme/repo:del-1",
        payload_digest="digest-1",
        decision="admitted_pending",
        execution_ref="",
        received_at_epoch=1700000000.0,
    )
    decision = _decide(
        _delivery(received_at_epoch=1700000000.0),
        (_config(),),
        digest="digest-1",
        stored=stored,
        now=1700000000.0 + 1.0,
    )
    assert decision.outcome == "admitted"
    assert decision.reason_code == "reattempt_pending_dispatch"


# ---------------------------------------------------------------------------
# R5: bounded retention, no silent replay of old spending intent
# ---------------------------------------------------------------------------


def test_receipt_retention_window():
    assert RECEIPT_RETENTION_SECONDS == 7 * 24 * 3600
    assert is_receipt_expired(
        received_at_epoch=1000.0, now_epoch=1000.0 + RECEIPT_RETENTION_SECONDS + 1.0
    )
    assert not is_receipt_expired(
        received_at_epoch=1000.0, now_epoch=1000.0 + RECEIPT_RETENTION_SECONDS - 1.0
    )


def test_expired_delivery_gets_safe_disposition_not_launch():
    old = _delivery(received_at_epoch=1000.0)
    decision = _decide(old, (_config(),), now=1000.0 + RECEIPT_RETENTION_SECONDS + 10.0)
    assert decision.outcome == "rejected"
    assert decision.reason_code == "stale_event"


def test_expired_stored_receipt_is_never_fresh_spending_intent():
    """A weeks-old pending receipt redelivered by hand is rejected, not run."""
    now = 2000000000.0
    received = now - RECEIPT_RETENTION_SECONDS - 10.0
    for stored_decision, execution_ref in (
        ("admitted_pending", ""),
        ("admitted_dispatched", "mm:deadbeef"),
    ):
        stored = StoredReceipt(
            delivery_key="github-delivery:v1:12345:acme/repo:del-old",
            payload_digest="digest-1",
            decision=stored_decision,
            execution_ref=execution_ref,
            received_at_epoch=received,
        )
        decision = _decide(
            _delivery(delivery_id="del-old", received_at_epoch=now),
            (_config(),),
            stored=stored,
            now=now,
        )
        assert decision.outcome == "rejected", stored_decision
        assert decision.reason_code == "stale_event"


def test_fresh_stored_receipt_still_reuses_or_reattempts():
    now = 2000000000.0
    stored = StoredReceipt(
        delivery_key="github-delivery:v1:12345:acme/repo:del-1",
        payload_digest="digest-1",
        decision="admitted_dispatched",
        execution_ref="mm:live",
        received_at_epoch=now - 60.0,
    )
    decision = _decide(
        _delivery(received_at_epoch=now), (_config(),), stored=stored, now=now
    )
    assert decision.outcome == "redelivery_reuse"
    assert decision.execution_ref == "mm:live"


def test_pr_backed_issue_is_untrusted_fork_content_by_default():
    """An issues payload with an issue.pull_request marker cannot prove a
    non-fork head from payload fields, so default-deny treats it as fork."""
    payload = {
        "action": "labeled",
        "installation": {"id": 12345},
        "repository": {"full_name": "acme/repo"},
        "sender": {"login": "alice", "type": "User"},
        "label": {"name": "mm-ready"},
        "issue": {"number": 7, "pull_request": {"url": "https://api.github.com/x"}},
    }
    delivery = delivery_from_webhook_payload(
        delivery_id="del-pr",
        event_name="issues",
        payload=payload,
        received_at_epoch=1700000000.0,
    )
    assert delivery.is_fork_content is True
    decision = _decide(delivery, (_config(),))
    assert decision.outcome == "rejected"
    assert decision.reason_code == "fork_content_untrusted"


def test_pr_backed_issue_runs_when_fork_content_opted_in():
    payload = {
        "action": "labeled",
        "installation": {"id": 12345},
        "repository": {"full_name": "acme/repo"},
        "sender": {"login": "alice", "type": "User"},
        "label": {"name": "mm-ready"},
        "issue": {"number": 7, "pull_request": {"url": "https://api.github.com/x"}},
    }
    delivery = delivery_from_webhook_payload(
        delivery_id="del-pr",
        event_name="issues",
        payload=payload,
        received_at_epoch=1700000000.0,
    )
    decision = _decide(delivery, (_config(allow_fork_content=True),))
    assert decision.outcome == "admitted"
