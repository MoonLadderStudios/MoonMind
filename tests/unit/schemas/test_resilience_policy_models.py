"""Contract tests for the versioned ResiliencePolicy envelope (MM-880)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.resilience_policy_models import (
    RESILIENCE_POLICY_CONTENT_TYPE,
    RESILIENCE_POLICY_SCHEMA_VERSION,
    ResiliencePolicyEnvelope,
    ResiliencePolicyError,
    ResiliencePolicyRef,
    compile_resilience_policy,
)


def _valid_kwargs(**overrides):
    kwargs = dict(
        compiled_at=datetime(2026, 6, 23, 12, 0, 0, tzinfo=UTC),
        attempts={
            "stepMaxAttempts": 3,
            "stepNoProgressLimit": 2,
            "jobSelfHealMaxResets": 1,
        },
        timeouts={"stepTimeoutSeconds": 900, "stepIdleTimeoutSeconds": 300},
        provider_cooldown={
            "cooldownAfter429Seconds": 900,
            "providerProfileId": "prof-1",
            "rateLimitPolicy": {"strategy": "slot_cooldown"},
        },
        checkpoints={
            "checkpointRequired": True,
            "requiredBoundaries": [
                "after_prepare",
                "before_execution",
                "after_execution",
            ],
        },
        idempotency={
            "sideEffectIdempotencyRequired": True,
            "keyStrategy": "step_execution_operation",
        },
        outbound_scanning={"highSecurityMode": False, "blockOnFinding": False},
        observability={
            "liveLogsTimelineEnabled": False,
            "structuredHistoryEnabled": True,
        },
        cost_attribution={
            "runtimeId": "codex_cli",
            "model": "claude-opus-4-8",
            "effort": "high",
        },
        workflow_id="wf-1",
        run_id="run-1",
    )
    kwargs.update(overrides)
    return kwargs


def test_compile_produces_versioned_envelope() -> None:
    env = compile_resilience_policy(**_valid_kwargs())

    assert env.schema_version == RESILIENCE_POLICY_SCHEMA_VERSION
    assert env.content_type == RESILIENCE_POLICY_CONTENT_TYPE
    assert env.policy_version == 1
    assert env.policy_id and env.policy_id.startswith("resilience-policy-")
    assert env.digest and len(env.digest) == 64
    # All governed dimensions are present and typed.
    assert env.attempts.step_max_attempts == 3
    assert env.timeouts.step_idle_timeout_seconds == 300
    assert env.provider_cooldown.cooldown_after_429_seconds == 900
    assert env.provider_cooldown.rate_limit_policy == {"strategy": "slot_cooldown"}
    assert env.checkpoints.required_boundaries[0] == "after_prepare"
    assert env.idempotency.side_effect_idempotency_required is True
    assert env.outbound_scanning.high_security_mode is False
    assert env.observability.structured_history_enabled is True
    assert env.cost_attribution.model == "claude-opus-4-8"


def test_digest_is_deterministic_and_identity_independent() -> None:
    env_a = compile_resilience_policy(**_valid_kwargs())
    # Different compile timestamp and run identity, identical governing values.
    env_b = compile_resilience_policy(
        **_valid_kwargs(
            compiled_at=datetime(2030, 1, 1, tzinfo=UTC),
            workflow_id="wf-2",
            run_id="run-2",
        )
    )

    assert env_a.digest == env_b.digest
    assert env_a.policy_id == env_b.policy_id


def test_digest_changes_when_governing_value_changes() -> None:
    env_a = compile_resilience_policy(**_valid_kwargs())
    env_b = compile_resilience_policy(
        **_valid_kwargs(
            timeouts={"stepTimeoutSeconds": 1200, "stepIdleTimeoutSeconds": 300},
        )
    )

    assert env_a.digest != env_b.digest
    assert env_a.policy_id != env_b.policy_id


def test_roundtrip_validation_and_compact_ref() -> None:
    env = compile_resilience_policy(**_valid_kwargs())
    dumped = env.model_dump(by_alias=True, mode="json")

    # Re-validation reproduces the same digest/id (digest verified, not recomputed blindly).
    reloaded = ResiliencePolicyEnvelope.model_validate(dumped)
    assert reloaded.digest == env.digest
    assert reloaded.policy_id == env.policy_id

    ref = env.compact_ref(envelope_ref="artifact://abc123")
    assert isinstance(ref, ResiliencePolicyRef)
    assert ref.policy_id == env.policy_id
    assert ref.policy_version == env.policy_version
    assert ref.digest == env.digest
    assert ref.content_type == RESILIENCE_POLICY_CONTENT_TYPE
    assert ref.envelope_ref == "artifact://abc123"


def test_tampered_digest_is_rejected() -> None:
    env = compile_resilience_policy(**_valid_kwargs())
    dumped = env.model_dump(by_alias=True, mode="json")
    dumped["digest"] = "0" * 64

    with pytest.raises(ResiliencePolicyError):
        ResiliencePolicyEnvelope.model_validate(dumped)


def test_tampered_policy_id_is_rejected() -> None:
    env = compile_resilience_policy(**_valid_kwargs())
    dumped = env.model_dump(by_alias=True, mode="json")
    dumped["policyId"] = "resilience-policy-deadbeefdeadbeef"

    with pytest.raises(ResiliencePolicyError):
        ResiliencePolicyEnvelope.model_validate(dumped)


@pytest.mark.parametrize("missing_section", ["attempts", "timeouts", "checkpoints"])
def test_missing_section_fails_fast(missing_section: str) -> None:
    with pytest.raises(ResiliencePolicyError):
        compile_resilience_policy(**_valid_kwargs(**{missing_section: None}))


def test_unsupported_checkpoint_boundary_fails_fast() -> None:
    with pytest.raises(ResiliencePolicyError):
        compile_resilience_policy(
            **_valid_kwargs(
                checkpoints={
                    "checkpointRequired": True,
                    "requiredBoundaries": ["not_a_boundary"],
                }
            )
        )


def test_required_checkpoint_without_boundaries_fails_fast() -> None:
    with pytest.raises(ResiliencePolicyError):
        compile_resilience_policy(
            **_valid_kwargs(
                checkpoints={"checkpointRequired": True, "requiredBoundaries": []}
            )
        )


def test_unsupported_idempotency_strategy_fails_fast() -> None:
    with pytest.raises(ResiliencePolicyError):
        compile_resilience_policy(
            **_valid_kwargs(
                idempotency={
                    "sideEffectIdempotencyRequired": True,
                    "keyStrategy": "made_up_strategy",
                }
            )
        )


def test_invalid_attempt_budget_fails_fast() -> None:
    with pytest.raises(ResiliencePolicyError):
        compile_resilience_policy(
            **_valid_kwargs(
                attempts={
                    "stepMaxAttempts": 0,
                    "stepNoProgressLimit": 2,
                    "jobSelfHealMaxResets": 1,
                }
            )
        )


def test_secret_refs_must_be_references_not_raw_values() -> None:
    # A real reference is accepted.
    env = compile_resilience_policy(
        **_valid_kwargs(secret_refs={"providerToken": "secret://vault/provider"})
    )
    assert env.secret_refs["providerToken"] == "secret://vault/provider"

    # A raw secret-looking value is rejected (references only).
    with pytest.raises(ResiliencePolicyError):
        compile_resilience_policy(
            **_valid_kwargs(secret_refs={"providerToken": "ghp_rawtokenvalue123"})
        )


def test_rate_limit_policy_rejects_secret_like_keys() -> None:
    with pytest.raises(ResiliencePolicyError):
        compile_resilience_policy(
            **_valid_kwargs(
                provider_cooldown={
                    "cooldownAfter429Seconds": 900,
                    "providerProfileId": "prof-1",
                    "rateLimitPolicy": {"api_key": "raw-secret-value"},
                }
            )
        )


def test_refs_reject_inline_content() -> None:
    # Newline-bearing "ref" is inline content, not a compact reference.
    with pytest.raises(ResiliencePolicyError):
        compile_resilience_policy(
            **_valid_kwargs(details_ref="line1\nline2")
        )

    # Over-long ref is rejected as well.
    with pytest.raises(ResiliencePolicyError):
        compile_resilience_policy(**_valid_kwargs(details_ref="a" * 2000))


def test_artifact_backed_details_ref_is_preserved() -> None:
    env = compile_resilience_policy(
        **_valid_kwargs(details_ref="artifact://resilience/details")
    )
    assert env.details_ref == "artifact://resilience/details"
