"""Unit tests for the canonical model-resolution helper.

Tests the three-level precedence:
  1. task_override  (explicit requested model)
  2. provider_profile_default  (profile.default_model)
  3. runtime_default  (canonical registry)
  4. none  (nothing available)

Also tests that `normalize_runtime_id` applies aliases correctly.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from moonmind.workflows.executions.model_resolver import (
    resolve_effective_model,
    resolve_model_effort,
)
from moonmind.workflows.executions.runtime_defaults import normalize_runtime_id

# ---------------------------------------------------------------------------
# normalize_runtime_id
# ---------------------------------------------------------------------------

class TestNormalizeRuntimeId:
    def test_codex_cli_passthrough(self):
        assert normalize_runtime_id("codex_cli") == "codex_cli"

    def test_claude_code_passthrough(self):
        assert normalize_runtime_id("claude_code") == "claude_code"

    def test_codex_alias_normalized(self):
        """Short alias 'codex' must map to canonical 'codex_cli'."""
        assert normalize_runtime_id("codex") == "codex_cli"

    def test_claude_alias_normalized(self):
        """Short alias 'claude' must map to canonical 'claude_code'."""
        assert normalize_runtime_id("claude") == "claude_code"

    def test_uppercase_normalized(self):
        assert normalize_runtime_id("CODEX") == "codex_cli"
        assert normalize_runtime_id("CLAUDE_CODE") == "claude_code"

    def test_unknown_runtime_passthrough(self):
        assert normalize_runtime_id("some_future_runtime") == "some_future_runtime"

    def test_omnigent_product_selector_is_not_normalized_to_codex(self):
        assert normalize_runtime_id("Omnigent") == "omnigent"

    def test_none_falls_back_to_default(self):
        # None / empty falls back to DEFAULT_WORKFLOW_RUNTIME
        result = normalize_runtime_id(None)
        assert result  # should return *some* string, not explode

# ---------------------------------------------------------------------------
# resolve_effective_model: task_override
# ---------------------------------------------------------------------------

class TestResolveEffectiveModelTaskOverride:
    def test_explicit_model_wins_over_everything(self):
        profile = MagicMock()
        profile.default_model = "profile-model"
        model, source = resolve_effective_model(
            runtime_id="codex_cli",
            profile=profile,
            requested_model="my-special-model",
        )
        assert model == "my-special-model"
        assert source == "task_override"

    def test_explicit_model_wins_even_without_profile(self):
        model, source = resolve_effective_model(
            runtime_id="codex_cli",
            profile=None,
            requested_model="explicit-model",
        )
        assert model == "explicit-model"
        assert source == "task_override"

    def test_whitespace_only_model_is_not_task_override(self):
        """Whitespace-only requested_model should be treated as absent."""
        model, source = resolve_effective_model(
            runtime_id="codex_cli",
            profile=None,
            requested_model="   ",
        )
        # Falls through to runtime default since profile is None
        assert source == "runtime_default"

# ---------------------------------------------------------------------------
# resolve_effective_model: provider_profile_default
# ---------------------------------------------------------------------------

class TestResolveEffectiveModelProfileDefault:
    def test_profile_default_overrides_runtime_default(self):
        profile = MagicMock()
        profile.default_model = "profile-4.0"
        model, source = resolve_effective_model(
            runtime_id="codex_cli",
            profile=profile,
            requested_model=None,
        )
        assert model == "profile-4.0"
        assert source == "provider_profile_default"

    def test_empty_profile_default_falls_through(self):
        profile = MagicMock()
        profile.default_model = ""
        model, source = resolve_effective_model(
            runtime_id="codex_cli",
            profile=profile,
            requested_model=None,
        )
        assert source == "runtime_default"

    def test_none_profile_default_falls_through(self):
        profile = MagicMock()
        profile.default_model = None
        model, source = resolve_effective_model(
            runtime_id="codex_cli",
            profile=profile,
            requested_model=None,
        )
        assert source == "runtime_default"

# ---------------------------------------------------------------------------
# resolve_effective_model: runtime_default
# ---------------------------------------------------------------------------

class TestResolveEffectiveModelRuntimeDefault:
    @pytest.mark.parametrize(
        "runtime_id,expected_model",
        [
            ("codex_cli", "gpt-5.5"),
            ("claude_code", "claude-opus-4-8"),
        ],
    )
    def test_runtime_default_for_each_canonical_id(self, runtime_id, expected_model):
        # Pass an empty env so the test asserts the in-code runtime default
        # rather than any ambient MOONMIND_*_MODEL / *_MODEL override that may
        # be set in the managed-agent container the unit suite runs inside.
        model, source = resolve_effective_model(
            runtime_id=runtime_id,
            profile=None,
            requested_model=None,
            env={},
        )
        assert model == expected_model
        assert source == "runtime_default"

    def test_codex_alias_resolves_runtime_default(self):
        """Alias 'codex' must produce the same result as canonical 'codex_cli'."""
        model_canonical, _ = resolve_effective_model(
            runtime_id="codex_cli", profile=None, requested_model=None
        )
        model_alias, source = resolve_effective_model(
            runtime_id="codex", profile=None, requested_model=None
        )
        assert model_alias == model_canonical
        assert source == "runtime_default"

    def test_claude_alias_resolves_runtime_default(self):
        """Alias 'claude' must produce the same result as canonical 'claude_code'."""
        model_canonical, _ = resolve_effective_model(
            runtime_id="claude_code", profile=None, requested_model=None
        )
        model_alias, source = resolve_effective_model(
            runtime_id="claude", profile=None, requested_model=None
        )
        assert model_alias == model_canonical
        assert source == "runtime_default"

# ---------------------------------------------------------------------------
# resolve_effective_model: none
# ---------------------------------------------------------------------------

class TestResolveEffectiveModelNone:
    def test_unknown_runtime_no_profile_returns_none(self):
        model, source = resolve_effective_model(
            runtime_id="unknown_future_runtime",
            profile=None,
            requested_model=None,
        )
        assert model is None
        assert source == "none"

    def test_no_runtime_resolves_to_default_runtime(self):
        model, source = resolve_effective_model(
            runtime_id=None,
            profile=None,
            requested_model=None,
            env={},
        )
        assert model == "gpt-5.5"
        assert source == "runtime_default"

# ---------------------------------------------------------------------------
# Precedence ordering (all three levels)
# ---------------------------------------------------------------------------

class TestPrecedenceOrder:
    """Ensure the exact precedence: task > profile > runtime."""

    def _profile_with_model(self, model: str) -> MagicMock:
        p = MagicMock()
        p.default_model = model
        return p

    def test_task_beats_profile_beats_runtime(self):
        profile = self._profile_with_model("profile-model")
        model, source = resolve_effective_model(
            runtime_id="codex_cli",
            profile=profile,
            requested_model="task-model",
        )
        assert model == "task-model"
        assert source == "task_override"

    def test_profile_beats_runtime_when_no_task(self):
        profile = self._profile_with_model("profile-model")
        model, source = resolve_effective_model(
            runtime_id="codex_cli",
            profile=profile,
            requested_model=None,
        )
        assert model == "profile-model"
        assert source == "provider_profile_default"

    def test_runtime_used_when_no_task_no_profile_default(self):
        profile = self._profile_with_model("")  # blank, same as None
        model, source = resolve_effective_model(
            runtime_id="codex_cli",
            profile=profile,
            requested_model=None,
            env={},
        )
        assert model == "gpt-5.5"
        assert source == "runtime_default"


class TestResolveModelEffortTiers:
    def _profile(self, **overrides):
        profile = {
            "enabled": True,
            "auth_state": "connected",
            "default_model": None,
            "default_effort": None,
            "model_tiers": [
                {
                    "label": "Tier 1",
                    "model": "tier-1-model",
                    "effort": "low",
                    "parameters": {"temperature": 0},
                },
                {
                    "label": "Tier 2",
                    "model": "tier-2-model",
                    "effort": "high",
                    "parameters": {"temperature": 1},
                },
            ],
            "default_model_tier": 1,
        }
        profile.update(overrides)
        return profile

    def test_requested_tier_2_resolves_to_model_tiers_index_1(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(default_model_tier=1),
            requested_model_tier=2,
            env={},
        )

        assert resolved.model == "tier-2-model"
        assert resolved.effort == "high"
        assert resolved.requested_model_tier == 2
        assert resolved.effective_model_tier == 2
        assert resolved.tier_label == "Tier 2"
        assert resolved.model_source == "requested_tier"
        assert resolved.effort_source == "requested_tier"
        assert resolved.fallback_reason is None
        assert resolved.effort_application_status == "unknown"
        assert resolved.tier_parameters == {"temperature": 1}

    def test_requested_tier_does_not_use_default_model_tier(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(default_model_tier=1),
            requested_model_tier=2,
            env={},
        )

        assert resolved.effective_model_tier == 2
        assert resolved.model == "tier-2-model"

    def test_requested_tier_above_configured_range_clamps_by_default(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(),
            requested_model_tier=3,
            env={},
        )

        assert resolved.requested_model_tier == 3
        assert resolved.effective_model_tier == 2
        assert resolved.model == "tier-2-model"
        assert resolved.fallback_reason == "requested_tier_above_configured_range"

    def test_advisory_preview_mismatch_is_detected(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(),
            requested_model_tier=2,
            advisory_preview={
                "requestedTier": 2,
                "effectiveTier": 2,
                "model": "stale-model",
                "effort": "high",
                "fallbackReason": None,
            },
            env={},
        )

        assert resolved.preview_mismatch is True
        assert resolved.as_metadata()["previewMismatch"] is True

    def test_advisory_preview_can_resolve_non_launch_ready_profile(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(enabled=False),
            requested_model_tier=1,
            require_launch_ready=False,
            env={},
        )

        assert resolved.model == "tier-1-model"

    def test_strict_tier_fallback_rejects_unavailable_requested_tier(self):
        with pytest.raises(ValueError, match="requested_model_tier_unavailable"):
            resolve_model_effort(
                runtime_id="codex_cli",
                profile=self._profile(),
                requested_model_tier=3,
                tier_fallback="strict",
                env={},
            )

    def test_explicit_model_and_effort_bypass_tier_policy(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(),
            requested_model_tier=2,
            requested_model="task-model",
            requested_effort="xhigh",
            env={},
        )

        assert resolved.model == "task-model"
        assert resolved.effort == "xhigh"
        assert resolved.effective_model_tier is None
        assert resolved.tier_label is None
        assert resolved.model_source == "task_override"
        assert resolved.effort_source == "task_override"
        assert resolved.fallback_reason is None

    def test_explicit_model_only_bypasses_tier_policy_independently(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(default_effort="profile-effort"),
            requested_model_tier=2,
            requested_model="task-model",
            env={},
        )

        assert resolved.model == "task-model"
        assert resolved.effort == "profile-effort"
        assert resolved.effective_model_tier is None
        assert resolved.tier_label is None
        assert resolved.model_source == "task_override"
        assert resolved.effort_source == "provider_profile_default"
        assert resolved.fallback_reason is None

    def test_explicit_effort_only_preserves_requested_tier_model(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(default_model="profile-model"),
            requested_model_tier=2,
            requested_effort="xhigh",
            env={},
        )

        assert resolved.model == "tier-2-model"
        assert resolved.effort == "xhigh"
        assert resolved.effective_model_tier == 2
        assert resolved.tier_label == "Tier 2"
        assert resolved.model_source == "requested_tier"
        assert resolved.effort_source == "task_override"
        assert resolved.fallback_reason is None

    @pytest.mark.parametrize("bad_tier", [0, -1, 1.5, "2", True])
    def test_model_tier_must_be_integer_greater_than_or_equal_to_1(self, bad_tier):
        with pytest.raises(ValueError, match="modelTier"):
            resolve_model_effort(
                runtime_id="codex_cli",
                profile=self._profile(),
                requested_model_tier=bad_tier,  # type: ignore[arg-type]
                env={},
            )

    def test_missing_selected_profile_fails_explicitly(self):
        with pytest.raises(ValueError, match="selected provider profile is required"):
            resolve_model_effort(
                runtime_id="codex_cli",
                profile=None,
                requested_model_tier=1,
                env={},
            )

    @pytest.mark.parametrize(
        "profile",
        [
            {"enabled": False, "auth_state": "connected", "model_tiers": []},
            {"enabled": True, "auth_state": "disconnected", "model_tiers": []},
            {
                "enabled": True,
                "auth_state": "connected",
                "disabled_reason": "user_disabled",
                "model_tiers": [],
            },
        ],
    )
    def test_launch_unready_selected_profile_fails_explicitly(self, profile):
        with pytest.raises(ValueError, match="not launch-ready"):
            resolve_model_effort(
                runtime_id="codex_cli",
                profile=profile,
                requested_model_tier=1,
                env={},
            )

    def test_launch_unready_object_profile_checks_camel_case_attributes(self):
        profile = SimpleNamespace(
            enabled=True,
            authState="disconnected",
            model_tiers=[],
        )

        with pytest.raises(ValueError, match="not launch-ready"):
            resolve_model_effort(
                runtime_id="codex_cli",
                profile=profile,
                requested_model_tier=1,
                env={},
            )

    def test_legacy_profile_and_runtime_defaults_remain_after_tiers(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(
                model_tiers=[{"label": "Tier 1", "model": None, "effort": None}],
                default_model="legacy-model",
                default_effort="legacy-effort",
            ),
            requested_model_tier=1,
            env={},
        )

        assert resolved.model == "legacy-model"
        assert resolved.effort == "legacy-effort"
        assert resolved.model_source == "provider_profile_default"
        assert resolved.effort_source == "provider_profile_default"
