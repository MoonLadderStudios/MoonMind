"""Hermetic tests for the bounded @mm PR-command vocabulary (#763).

Covers the deterministic parser fixture matrix, event eligibility, the
authorization gate, preflight (including the non-main-base contract),
stable command identity/redelivery, revalidation, competing-run resolution,
Skill delegation (resolve grants no merge permission), loop-safe redacted
feedback, and the portable fix-merge-conflicts Skill regression.
"""

from __future__ import annotations

from pathlib import Path

from moonmind.workflows.adapters.github_pr_commands import (
    AuthorizationRequest,
    CommandJourneyResult,
    PreflightRequest,
    VerifiedCommandEvent,
    build_feedback_body,
    classify_feedback_write_outcome,
    classify_redelivery,
    command_identity,
    evaluate_command_preflight,
    evaluate_dispatch_authorization,
    evaluate_event_eligibility,
    feedback_triggers_bot,
    freeze_command_dispatch,
    handle_verified_command_event,
    parse_pr_command,
    redact_for_feedback,
    resolve_competing_run,
    resolve_skill_binding,
    revalidate_before_mutation,
    scan_for_secrets,
)


def _authorized() -> AuthorizationRequest:
    return AuthorizationRequest(
        transport_verified=True,
        repository_opted_in=True,
        actor_authorized=True,
        connection_available=True,
        budget_available=True,
        publication_allowed=True,
    )


# ---------------------------------------------------------------------------
# R1: canonical parser
# ---------------------------------------------------------------------------


def test_canonical_commands_dispatch_to_existing_skills():
    assert parse_pr_command("@mm fix comments").outcome == "dispatch"
    assert parse_pr_command("@mm fix comments").skill_id == "fix-comments"
    assert parse_pr_command("@mm fix merge conflicts").skill_id == "fix-merge-conflicts"
    assert parse_pr_command("@mm resolve").skill_id == "pr-resolver"


def test_parser_whitespace_and_case_variants():
    assert parse_pr_command("  @mm fix comments  ").outcome == "dispatch"
    assert parse_pr_command("@MM FIX COMMENTS").outcome == "dispatch"
    assert parse_pr_command("@mm   Fix   Merge   Conflicts").outcome == "dispatch"
    assert parse_pr_command("@mm\tResolve").outcome == "dispatch"
    parsed = parse_pr_command("@mm   Fix   Merge   Conflicts")
    assert parsed.normalized_command == "fix merge conflicts"


def test_ordinary_conversation_is_ignored():
    assert parse_pr_command("Looks good, thanks!").outcome == "ignore"
    assert parse_pr_command("").outcome == "ignore"
    assert parse_pr_command(None).outcome == "ignore"
    assert parse_pr_command("   \n  ").outcome == "ignore"


def test_unknown_explicit_command_gets_bounded_help_not_dispatch():
    parsed = parse_pr_command("@mm frobnicate")
    assert parsed.outcome == "help"
    assert parsed.reason_code == "unknown_command"
    # Unsupported suffixes are unknown commands: help, never dispatch.
    suffixed = parse_pr_command("@mm fix comments please")
    assert suffixed.outcome == "help"
    assert parse_pr_command("@mm").outcome == "help"


# ---------------------------------------------------------------------------
# R9: negative guards
# ---------------------------------------------------------------------------


def test_quoted_fenced_code_and_hidden_html_never_dispatch():
    assert parse_pr_command("> @mm fix comments").outcome == "ignore"
    assert parse_pr_command("```\n@mm fix comments\n```").outcome == "ignore"
    assert parse_pr_command("`@mm fix comments`").outcome == "ignore"
    assert parse_pr_command("<!-- @mm fix comments -->").outcome == "ignore"
    assert parse_pr_command("@mm fix comments <!-- hidden -->").outcome == "ignore"


def test_prose_embedded_mention_is_ignored():
    assert (
        parse_pr_command("please @mm fix comments when free").outcome == "ignore"
    )


def test_multiple_commands_and_trailing_prose_are_ambiguous():
    assert (
        parse_pr_command("@mm fix comments\n@mm resolve").outcome == "ignore"
    )
    assert (
        parse_pr_command("@mm fix comments\nPlease hurry").outcome == "ignore"
    )


def test_event_eligibility_rejects_edited_inline_and_bot_loops():
    parsed = parse_pr_command("@mm fix comments")
    eligible, _ = evaluate_event_eligibility(parsed)
    assert eligible is True
    assert evaluate_event_eligibility(parsed, is_edited=True)[0] is False
    assert evaluate_event_eligibility(parsed, is_inline=True)[0] is False
    assert evaluate_event_eligibility(parsed, is_bot_actor=True)[0] is False
    assert (
        evaluate_event_eligibility(
            parsed, is_bot_actor=True, trusted_automation_permitted=True
        )[0]
        is True
    )
    assert (
        evaluate_event_eligibility(parse_pr_command("hello"))[0] is False
    )


# ---------------------------------------------------------------------------
# R2: authorization gate fails closed, no fallback search
# ---------------------------------------------------------------------------


def test_authorization_gate_allows_only_fully_authorized():
    assert evaluate_dispatch_authorization(_authorized()).allowed is True


def test_authorization_gate_fails_closed_per_fact():
    base = _authorized()
    cases = [
        (base.__class__(**{**base.__dict__, "transport_verified": False}), "transport_unverified"),
        (base.__class__(**{**base.__dict__, "repository_opted_in": False}), "repository_not_opted_in"),
        (base.__class__(**{**base.__dict__, "actor_authorized": False}), "actor_not_authorized"),
        (base.__class__(**{**base.__dict__, "connection_available": False}), "connection_unavailable"),
        (base.__class__(**{**base.__dict__, "budget_available": False}), "budget_exceeded"),
        (base.__class__(**{**base.__dict__, "publication_allowed": False}), "publication_blocked"),
    ]
    for request, reason in cases:
        decision = evaluate_dispatch_authorization(request)
        assert decision.allowed is False
        assert decision.reason_code == reason


def test_authorization_gate_defaults_to_denied():
    decision = evaluate_dispatch_authorization(AuthorizationRequest())
    assert decision.allowed is False
    assert decision.reason_code == "transport_unverified"


# ---------------------------------------------------------------------------
# R3/R11: preflight incl. non-main base, forks, revoked permissions
# ---------------------------------------------------------------------------


def _ready_preflight(**overrides) -> PreflightRequest:
    base = {
        "skill_id": "fix-merge-conflicts",
        "pr_base_ref": "release/2.x",
        "pr_base_sha": "b" * 40,
        "pr_head_sha": "a" * 40,
        "is_fork": False,
        "fork_write_permitted": False,
        "branch_write_authorized": True,
        "permissions_revoked": False,
        "skill_capability_supported": True,
    }
    base.update(overrides)
    return PreflightRequest(**base)


def test_preflight_merge_target_uses_actual_base_never_silent_main():
    decision = evaluate_command_preflight(_ready_preflight())
    assert decision.ready is True
    assert decision.merge_target_ref == "origin/release/2.x"
    main_decision = evaluate_command_preflight(
        _ready_preflight(pr_base_ref="main")
    )
    assert main_decision.merge_target_ref == "origin/main"


def test_preflight_blocks_before_paid_work():
    assert (
        evaluate_command_preflight(_ready_preflight(pr_base_ref="")).reason_code
        == "unsupported_missing_base"
    )
    assert (
        evaluate_command_preflight(
            _ready_preflight(pr_base_sha="")
        ).reason_code
        == "unsupported_missing_base"
    )
    assert (
        evaluate_command_preflight(_ready_preflight(pr_head_sha="")).reason_code
        == "missing_head"
    )
    assert (
        evaluate_command_preflight(
            _ready_preflight(is_fork=True, fork_write_permitted=False)
        ).reason_code
        == "fork_write_unavailable"
    )
    assert (
        evaluate_command_preflight(
            _ready_preflight(branch_write_authorized=False)
        ).reason_code
        == "branch_write_unavailable"
    )
    assert (
        evaluate_command_preflight(
            _ready_preflight(permissions_revoked=True)
        ).reason_code
        == "permission_revoked"
    )
    assert (
        evaluate_command_preflight(
            _ready_preflight(
                skill_id="fix-merge-conflicts", skill_capability_supported=False
            )
        ).reason_code
        == "unsupported_skill_capability"
    )
    assert (
        evaluate_command_preflight(
            _ready_preflight(skill_id="nope")
        ).reason_code
        == "unsupported_skill_capability"
    )


# ---------------------------------------------------------------------------
# R4/R10: stable identity, redelivery, revalidation, competing runs
# ---------------------------------------------------------------------------


def _identity(**overrides):
    base = {
        "installation_id": "123",
        "repository": "MoonLadderStudios/MoonMind",
        "pr_number": 42,
        "comment_id": "999",
        "normalized_command": "fix comments",
        "comment_body": "@mm fix comments",
    }
    base.update(overrides)
    return command_identity(**base)


def test_command_identity_stable_and_scoped():
    first = _identity()
    second = _identity()
    assert first.identity_key == second.identity_key
    assert "999" in first.identity_key
    assert "fix-comments" in first.identity_key  # slug of normalized "fix comments"
    other_comment = _identity(comment_id="1000")
    assert other_comment.identity_key != first.identity_key
    other_command = _identity(normalized_command="resolve")
    assert other_command.identity_key != first.identity_key


def test_classify_redelivery_reuse_new_and_edited():
    stored = _identity()
    assert (
        classify_redelivery(
            stored_comment_id=stored.comment_id,
            stored_digest=stored.content_digest,
            incoming_comment_id=stored.comment_id,
            incoming_digest=stored.content_digest,
        )
        == "redelivery_reuse"
    )
    assert (
        classify_redelivery(
            stored_comment_id=stored.comment_id,
            stored_digest=stored.content_digest,
            incoming_comment_id="another",
            incoming_digest=stored.content_digest,
        )
        == "new_request"
    )
    assert (
        classify_redelivery(
            stored_comment_id=stored.comment_id,
            stored_digest=stored.content_digest,
            incoming_comment_id=stored.comment_id,
            incoming_digest="changed",
        )
        == "edited_unsupported"
    )


def test_freeze_and_revalidate_single_mutation_owner():
    frozen = freeze_command_dispatch(
        identity_key="key",
        skill_id="fix-comments",
        skill_snapshot_ref="snapshot-1",
        repository="MoonLadderStudios/MoonMind",
        pr_number=42,
        pr_head_sha="a" * 40,
        pr_base_ref="main",
        pr_base_sha="b" * 40,
        merge_target_ref="origin/main",
        connection_id="conn-1",
    )
    assert frozen.skill_snapshot_ref == "snapshot-1"
    fresh = revalidate_before_mutation(
        frozen_head_sha="a" * 40,
        frozen_base_sha="b" * 40,
        frozen_actor_authorized=True,
        current_head_sha="a" * 40,
        current_base_sha="b" * 40,
        current_actor_authorized=True,
    )
    assert fresh.fresh is True
    assert (
        revalidate_before_mutation(
            frozen_head_sha="a" * 40,
            frozen_base_sha="b" * 40,
            frozen_actor_authorized=True,
            current_head_sha="c" * 40,
            current_base_sha="b" * 40,
            current_actor_authorized=True,
        ).reason_code
        == "stale_head"
    )
    assert (
        revalidate_before_mutation(
            frozen_head_sha="a" * 40,
            frozen_base_sha="b" * 40,
            frozen_actor_authorized=True,
            current_head_sha="a" * 40,
            current_base_sha="d" * 40,
            current_actor_authorized=True,
        ).reason_code
        == "stale_base"
    )
    assert (
        revalidate_before_mutation(
            frozen_head_sha="a" * 40,
            frozen_base_sha="b" * 40,
            frozen_actor_authorized=True,
            current_head_sha="a" * 40,
            current_base_sha="b" * 40,
            current_actor_authorized=False,
        ).reason_code
        == "authz_revoked"
    )


def test_competing_runs_surface_without_second_scheduler():
    assert resolve_competing_run(has_conflicting_run=True).result == "conflicting"
    assert (
        resolve_competing_run(has_active_compatible_run=True).result
        == "reuse_active"
    )
    assert resolve_competing_run(has_queued_compatible=True).result == "queued"
    assert resolve_competing_run().result == "none"


# ---------------------------------------------------------------------------
# R6/R12: delegation preserves Skill-owned semantics
# ---------------------------------------------------------------------------


def test_skill_bindings_use_existing_identities_without_merge_grant():
    binding = resolve_skill_binding("fix comments")
    assert binding is not None and binding.skill_id == "fix-comments"
    binding = resolve_skill_binding("fix merge conflicts")
    assert binding is not None and binding.skill_id == "fix-merge-conflicts"
    binding = resolve_skill_binding("resolve")
    assert binding is not None and binding.skill_id == "pr-resolver"
    assert binding.grants_merge_permission is False
    assert binding.requires_publication_evidence is True
    assert resolve_skill_binding("frobnicate") is None


# ---------------------------------------------------------------------------
# R7/R13: loop-safe redacted feedback, auxiliary failures
# ---------------------------------------------------------------------------


def test_feedback_body_carries_state_and_workflow_link_safely():
    body = build_feedback_body(
        command_label="@mm fix comments",
        skill_id="fix-comments",
        state="queued",
        workflow_ref="https://workflows.example/runs/1",
    )
    assert "queued" in body
    assert "https://workflows.example/runs/1" in body
    assert feedback_triggers_bot(body) is False


def test_feedback_states_are_distinct_and_safe_default():
    for state in (
        "queued",
        "running",
        "blocked",
        "terminal_success",
        "terminal_partial",
        "terminal_unavailable",
    ):
        body = build_feedback_body(
            command_label="@mm resolve",
            skill_id="pr-resolver",
            state=state,
            workflow_ref="run-1",
        )
        assert state in body
    fallback = build_feedback_body(
        command_label="@mm resolve",
        skill_id="pr-resolver",
        state="bogus",
        workflow_ref="run-1",
    )
    assert "blocked" in fallback


def test_feedback_never_leaks_secrets_or_raw_context():
    body = build_feedback_body(
        command_label="@mm fix comments",
        skill_id="fix-comments",
        state="blocked",
        workflow_ref="run-1",
        detail="token=ghp_abcdefghijklmnop raw diagnostics",
    )
    assert "ghp_abcdefghijklmnop" not in body
    assert "token=ghp" not in body
    assert scan_for_secrets(body) == []
    assert scan_for_secrets("see ghp_abcdefghijklmnop here") == ["github_token"]
    assert "[redacted]" in redact_for_feedback("password=supersecret value")


def test_feedback_write_failures_are_auxiliary():
    assert classify_feedback_write_outcome(True) == "feedback_posted"
    assert classify_feedback_write_outcome(False) == "feedback_auxiliary_failure"


# ---------------------------------------------------------------------------
# R14: portable Skill regression (no silent origin/main)
# ---------------------------------------------------------------------------


def test_fix_merge_conflicts_skill_has_no_silent_main_substitution():
    skill_path = (
        Path(__file__).resolve().parents[5]
        / ".agents"
        / "skills"
        / "fix-merge-conflicts"
        / "SKILL.md"
    )
    text = skill_path.read_text(encoding="utf-8")
    assert "origin/main" not in text
    assert "inputs.base" in text
    assert "base_unavailable" in text


# ---------------------------------------------------------------------------
# R6/R8/R10: hermetic per-command journey (transport receipt -> feedback)
# ---------------------------------------------------------------------------
#
# This is the complete production-boundary journey for this issue's
# "parsing and command semantics, not another executor" scope: the #3967
# transport receiver and Temporal dispatch/recovery workflow bind to
# handle_verified_command_event as their handoff contract. Durability
# (idempotency store, redelivery, restart recovery) lives with #3967,
# which stores the stable identity_key, reuses the existing
# dispatch/result on redelivery_reuse, and re-runs revalidation plus
# competing-run resolution before mutation.


def _journey_event(
    body: str, *, pr_base_ref: str = "release/2.x", **overrides
) -> VerifiedCommandEvent:
    base = {
        "comment_body": body,
        "installation_id": "123",
        "repository": "MoonLadderStudios/MoonMind",
        "pr_number": 42,
        "comment_id": "999",
        "authorization": _authorized(),
        "pr_base_ref": pr_base_ref,
        "pr_base_sha": "b" * 40,
        "pr_head_sha": "a" * 40,
        "branch_write_authorized": True,
        "skill_snapshot_ref": "snapshot-1",
        "connection_id": "conn-1",
        "workflow_ref": "https://workflows.example/runs/1",
    }
    base.update(overrides)
    return VerifiedCommandEvent(**base)


def test_journey_each_canonical_command_reaches_skill_dispatch():
    cases = [
        ("@mm fix comments", "fix-comments"),
        ("@mm fix merge conflicts", "fix-merge-conflicts"),
        ("@mm resolve", "pr-resolver"),
    ]
    for body, skill_id in cases:
        result = handle_verified_command_event(_journey_event(body))
        assert result.outcome == "dispatched"
        assert result.skill_id == skill_id
        assert result.dispatch is not None
        assert result.dispatch.skill_id == skill_id
        assert result.dispatch.skill_snapshot_ref == "snapshot-1"
        assert result.dispatch.connection_id == "conn-1"
        assert result.dispatch.pr_head_sha == "a" * 40
        # Non-main base is preserved end to end, never silent main.
        assert result.dispatch.merge_target_ref == "origin/release/2.x"
        assert result.identity_key == result.dispatch.identity_key
        # Queued feedback carries the workflow link and cannot retrigger.
        assert "queued" in result.feedback_body
        assert "https://workflows.example/runs/1" in result.feedback_body
        assert feedback_triggers_bot(result.feedback_body) is False
        # resolve preserves the declared publication policy: no merge grant.
        binding = resolve_skill_binding(result.normalized_command)
        assert binding is not None
        assert binding.grants_merge_permission is False
        assert binding.requires_publication_evidence is True


def test_journey_negative_paths_never_dispatch():
    assert (
        handle_verified_command_event(
            _journey_event("@mm frobnicate")
        ).outcome
        == "help"
    )
    ignored = handle_verified_command_event(
        _journey_event("Looks good, thanks!")
    )
    assert ignored.outcome == "ignored"
    assert ignored.dispatch is None
    edited = handle_verified_command_event(
        _journey_event("@mm fix comments", is_edited=True)
    )
    assert edited.outcome == "blocked"
    assert edited.dispatch is None
    revoked_authz = _authorized().__class__(
        **{**_authorized().__dict__, "actor_authorized": False}
    )
    revoked = handle_verified_command_event(
        _journey_event("@mm fix comments", authorization=revoked_authz)
    )
    assert revoked.outcome == "blocked"
    assert revoked.reason_code == "actor_not_authorized"
    assert revoked.dispatch is None
    fork_blocked = handle_verified_command_event(
        _journey_event("@mm fix comments", is_fork=True)
    )
    assert fork_blocked.outcome == "blocked"
    assert fork_blocked.reason_code == "fork_write_unavailable"


def test_journey_recovery_redelivery_reuse_and_stale_head_block():
    result = handle_verified_command_event(
        _journey_event("@mm fix comments")
    )
    assert result.outcome == "dispatched"
    assert result.dispatch is not None
    # Redelivery of the same comment body reuses the existing dispatch.
    redelivered = handle_verified_command_event(
        _journey_event("@mm fix comments")
    )
    assert redelivered.identity_key == result.identity_key
    assert (
        classify_redelivery(
            stored_comment_id="999",
            stored_digest=command_identity(
                installation_id="123",
                repository="MoonLadderStudios/MoonMind",
                pr_number=42,
                comment_id="999",
                normalized_command="fix comments",
                comment_body="@mm fix comments",
            ).content_digest,
            incoming_comment_id="999",
            incoming_digest=command_identity(
                installation_id="123",
                repository="MoonLadderStudios/MoonMind",
                pr_number=42,
                comment_id="999",
                normalized_command="fix comments",
                comment_body="@mm fix comments",
            ).content_digest,
        )
        == "redelivery_reuse"
    )
    # After a restart the dispatcher revalidates before mutation: a moved
    # head blocks instead of silently becoming a new billable request.
    assert (
        revalidate_before_mutation(
            frozen_head_sha=result.dispatch.pr_head_sha,
            frozen_base_sha=result.dispatch.pr_base_sha,
            frozen_actor_authorized=True,
            current_head_sha="c" * 40,
            current_base_sha=result.dispatch.pr_base_sha,
            current_actor_authorized=True,
        ).reason_code
        == "stale_head"
    )
    # A conflicting active run surfaces explicitly; the command layer never
    # invents another scheduler.
    assert (
        resolve_competing_run(has_conflicting_run=True).result
        == "conflicting"
    )
    assert isinstance(result, CommandJourneyResult)
