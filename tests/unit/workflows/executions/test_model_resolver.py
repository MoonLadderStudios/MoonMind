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
    RequestedModelTierUnavailableError,
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
        # Default runtime is now omnigent, which has no hard-coded model default
        assert model is None
        assert source == "none"

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
            "profile_id": "codex-profile",
            "profile_version": 42,
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

    def test_omitted_requested_tier_uses_profile_default_tier(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(default_model_tier=2),
            env={},
        )

        assert resolved.requested_model_tier is None
        assert resolved.effective_model_tier == 2
        assert resolved.model == "tier-2-model"
        assert resolved.effort == "high"
        assert resolved.model_source == "profile_default_tier"
        assert resolved.effort_source == "profile_default_tier"
        assert resolved.fallback_reason == "profile_default_tier"

    def test_requested_tier_below_configured_range_clamps_by_default(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(),
            requested_model_tier=0,
            env={},
        )

        assert resolved.requested_model_tier == 0
        assert resolved.effective_model_tier == 1
        assert resolved.model == "tier-1-model"
        assert resolved.fallback_reason == "requested_tier_below_configured_range"

    @pytest.mark.parametrize("tier_fallback", ["clamp", "strict"])
    def test_requested_tier_below_range_without_tiers_is_rejected(
        self, tier_fallback
    ):
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            resolve_model_effort(
                runtime_id="codex_cli",
                profile=self._profile(model_tiers=[]),
                requested_model_tier=0,
                tier_fallback=tier_fallback,
                env={},
            )

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
                "profileId": "codex-profile",
                "profileVersion": 42,
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

    def test_matching_advisory_preview_profile_identity_is_not_a_mismatch(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(),
            requested_model_tier=2,
            advisory_preview={
                "profileId": "codex-profile",
                "profileVersion": 42,
                "model": "tier-2-model",
                "effort": "high",
            },
            env={},
        )

        assert resolved.preview_mismatch is False

    @pytest.mark.parametrize(
        ("identity_field", "stale_value"),
        [("profileId", "other-profile"), ("profileVersion", 41)],
    )
    def test_advisory_preview_profile_identity_mismatch_is_detected(
        self, identity_field, stale_value
    ):
        advisory_preview = {
            "profileId": "codex-profile",
            "profileVersion": 42,
            "model": "tier-2-model",
            "effort": "high",
        }
        advisory_preview[identity_field] = stale_value

        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(),
            requested_model_tier=2,
            advisory_preview=advisory_preview,
            env={},
        )

        assert resolved.preview_mismatch is True

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
        with pytest.raises(RequestedModelTierUnavailableError) as exc_info:
            resolve_model_effort(
                runtime_id="codex_cli",
                profile=self._profile(),
                requested_model_tier=3,
                tier_fallback="strict",
                env={},
            )

        assert exc_info.value.code == "requested_model_tier_unavailable"
        assert exc_info.value.requested_model_tier == 3
        assert exc_info.value.configured_tier_count == 2

    def test_strict_tier_fallback_accepts_in_range_profile_default_tier(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(default_model_tier=2),
            tier_fallback="strict",
            env={},
        )

        assert resolved.effective_model_tier == 2
        assert resolved.fallback_reason == "profile_default_tier"

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

    @pytest.mark.parametrize("bad_tier", [1.5, "2", True])
    def test_model_tier_must_be_integer(self, bad_tier):
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

    def test_null_tier_fields_use_runtime_defaults_without_legacy_scalars(self):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile=self._profile(
                model_tiers=[{"label": "Tier 1", "model": None, "effort": None}],
                default_model="legacy-model",
                default_effort="legacy-effort",
            ),
            requested_model_tier=1,
            env={
                "MOONMIND_CODEX_MODEL": "runtime-model",
                "MOONMIND_CODEX_EFFORT": "medium",
            },
        )

        assert resolved.model == "runtime-model"
        assert resolved.effort == "medium"
        assert resolved.model_source == "runtime_default"
        assert resolved.effort_source == "runtime_default"


# ---------------------------------------------------------------------------
# MoonLadderStudios/MoonMind#4538: OpenRouter-via-OpenCode model inheritance
# ---------------------------------------------------------------------------

def _openrouter_profile(**overrides):
    base = {
        "profile_id": "opencode-openrouter",
        "runtime_id": "opencode",
        "provider_id": "openrouter",
        "default_model": None,
        "default_effort": None,
        "model_tiers": [],
        "enabled": True,
        "auth_state": "connected",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestOpenRouterModelInheritance:
    def test_compatible_openrouter_default_is_inherited(self):
        profile = _openrouter_profile()
        model, source = resolve_effective_model(
            runtime_id="opencode",
            profile=profile,
            requested_model=None,
            env={"MOONMIND_OMNIGENT_DEFAULT_MODEL": "openrouter/acme-model"},
        )
        assert model == "openrouter/acme-model"
        assert source == "runtime_default"

    def test_multi_slash_openrouter_id_preserved_exactly(self):
        profile = _openrouter_profile()
        model, source = resolve_effective_model(
            runtime_id="opencode",
            profile=profile,
            requested_model=None,
            env={"MOONMIND_OMNIGENT_DEFAULT_MODEL": "openrouter/acme/model-v1"},
        )
        assert model == "openrouter/acme/model-v1"
        assert source == "runtime_default"

    def test_mismatched_provider_default_stays_unresolved(self):
        profile = _openrouter_profile()
        for mismatched in (
            "opencode-go/muse-spark-1.3-contributor",
            "opencode/muse-spark-1.3-contributor-free",
            "openai/gpt-5.5",
            # Bare defaults never select the OpenRouter credential: only an
            # explicit ``openrouter/``-qualified default is inherited.
            "gpt-5.5",
        ):
            model, source = resolve_effective_model(
                runtime_id="opencode",
                profile=profile,
                requested_model=None,
                env={"MOONMIND_OMNIGENT_DEFAULT_MODEL": mismatched},
            )
            assert model is None, mismatched
            assert source == "none", mismatched

    def test_missing_default_stays_unresolved_without_stock_model(self):
        profile = _openrouter_profile()
        model, source = resolve_effective_model(
            runtime_id="opencode",
            profile=profile,
            requested_model=None,
            env={},
        )
        assert model is None
        assert source == "none"

    def test_explicit_overrides_keep_precedence(self):
        profile = _openrouter_profile(default_model="openrouter/profile-model")
        model, source = resolve_effective_model(
            runtime_id="opencode",
            profile=profile,
            requested_model=None,
            env={"MOONMIND_OMNIGENT_DEFAULT_MODEL": "openrouter/other-model"},
        )
        assert model == "openrouter/profile-model"
        assert source == "provider_profile_default"

        model, source = resolve_effective_model(
            runtime_id="opencode",
            profile=profile,
            requested_model="openrouter/task-model",
            env={"MOONMIND_OMNIGENT_DEFAULT_MODEL": "openrouter/other-model"},
        )
        assert model == "openrouter/task-model"
        assert source == "task_override"

    def test_tier_aware_inheritance_and_mismatch(self):
        profile = _openrouter_profile()
        resolved = resolve_model_effort(
            runtime_id="opencode",
            profile=profile,
            env={"MOONMIND_OMNIGENT_DEFAULT_MODEL": "openrouter/acme-model"},
        )
        assert resolved.model == "openrouter/acme-model"
        assert resolved.model_source == "runtime_default"
        # Inherited effort is not forced to a stock value.
        assert resolved.effort is None

        resolved = resolve_model_effort(
            runtime_id="opencode",
            profile=profile,
            env={"MOONMIND_OMNIGENT_DEFAULT_MODEL": "opencode-go/other"},
        )
        assert resolved.model is None
        assert resolved.model_source == "none"

    def test_default_change_affects_later_inherited_selections_only(self):
        profile = _openrouter_profile()
        first, _ = resolve_effective_model(
            runtime_id="opencode",
            profile=profile,
            requested_model=None,
            env={"MOONMIND_OMNIGENT_DEFAULT_MODEL": "openrouter/v1"},
        )
        second, _ = resolve_effective_model(
            runtime_id="opencode",
            profile=profile,
            requested_model=None,
            env={"MOONMIND_OMNIGENT_DEFAULT_MODEL": "openrouter/v2"},
        )
        assert first == "openrouter/v1"
        assert second == "openrouter/v2"
        # An explicit profile choice is unaffected by the default change.
        pinned = _openrouter_profile(default_model="openrouter/pinned")
        model, source = resolve_effective_model(
            runtime_id="opencode",
            profile=pinned,
            requested_model=None,
            env={"MOONMIND_OMNIGENT_DEFAULT_MODEL": "openrouter/v2"},
        )
        assert model == "openrouter/pinned"
        assert source == "provider_profile_default"


# ---------------------------------------------------------------------------
# Model-family tiers: automatic selection, pins, and admitted authority
# ---------------------------------------------------------------------------


def _family_profile(*, catalog=None, tiers=None, default_model_tier=2, **overrides):
    from moonmind.provider_profiles.model_families import (
        first_party_family_tier_template,
    )

    template_tiers, _ = first_party_family_tier_template("claude_code", "anthropic")
    profile = {
        "profile_id": "claude_anthropic_oauth",
        "provider_id": "anthropic",
        "enabled": True,
        "auth_state": "connected",
        "default_model": None,
        "default_effort": None,
        "model_tiers": tiers if tiers is not None else template_tiers,
        "default_model_tier": default_model_tier,
        "model_catalog_evidence_json": (
            None if catalog is None else {"models": [{"id": m} for m in catalog]}
        ),
    }
    profile.update(overrides)
    return profile


class TestModelFamilyTiers:
    def test_omitted_model_uses_default_tier_two_family_with_medium(self):
        resolved = resolve_model_effort(
            runtime_id="claude_code",
            profile=_family_profile(catalog=["claude-opus-5-5", "claude-sonnet-5-5"]),
            env={},
        )

        assert resolved.effective_model_tier == 2
        assert resolved.model == "claude-opus-5-5"
        assert resolved.effort == "medium"
        assert resolved.model_source == "profile_default_tier"
        assert resolved.model_family == "opus"
        assert resolved.model_mode == "automatic"
        assert resolved.family_resolution_status == "resolved"
        metadata = resolved.as_metadata()
        assert metadata["modelFamily"] == "opus"
        assert metadata["modelMode"] == "automatic"
        assert metadata["familyResolutionSource"] == "profile_catalog"

    def test_automatic_tier_follows_a_new_release_but_pins_do_not(self):
        from moonmind.provider_profiles.model_families import (
            first_party_family_tier_template,
        )

        tiers, _ = first_party_family_tier_template("claude_code", "anthropic")
        pinned_old = [dict(tier) for tier in tiers]
        pinned_old[1].update(model_mode="pinned", model="claude-opus-5")
        pinned_current = [dict(tier) for tier in tiers]
        pinned_current[1].update(model_mode="pinned", model="claude-opus-5-5")
        newer = ["claude-opus-5", "claude-opus-5-5", "claude-opus-6"]

        automatic = resolve_model_effort(
            runtime_id="claude_code", profile=_family_profile(catalog=newer), env={}
        )
        old_pin = resolve_model_effort(
            runtime_id="claude_code",
            profile=_family_profile(catalog=newer, tiers=pinned_old),
            env={},
        )
        current_pin = resolve_model_effort(
            runtime_id="claude_code",
            profile=_family_profile(catalog=newer, tiers=pinned_current),
            env={},
        )

        assert automatic.model == "claude-opus-6"
        assert old_pin.model == "claude-opus-5"
        assert old_pin.model_mode == "pinned"
        assert current_pin.model == "claude-opus-5-5"
        assert current_pin.family_resolution_status == "pinned"

    def test_unavailable_selected_family_never_falls_back(self):
        from moonmind.workflows.executions.model_resolver import (
            ModelTierFamilyUnavailableError,
        )

        profile = _family_profile(catalog=["claude-sonnet-5-5", "claude-haiku-5-5"])
        with pytest.raises(ModelTierFamilyUnavailableError) as excinfo:
            resolve_model_effort(runtime_id="claude_code", profile=profile, env={})
        assert excinfo.value.effective_model_tier == 2
        assert excinfo.value.family == "opus"
        assert "Opus" in str(excinfo.value)

        # Other tiers on the same profile stay usable.
        sonnet = resolve_model_effort(
            runtime_id="claude_code",
            profile=profile,
            requested_model_tier=3,
            env={},
        )
        assert sonnet.model == "claude-sonnet-5-5"

    def test_cold_start_resolution_is_reported_unverified(self):
        resolved = resolve_model_effort(
            runtime_id="claude_code", profile=_family_profile(catalog=None), env={}
        )
        assert resolved.model == "claude-opus-5-5"
        assert resolved.family_resolution_status == "unverified"
        assert resolved.family_resolution_source == "cold_start_placeholder"

    def test_admitted_selection_survives_a_catalog_update(self):
        admitted = resolve_model_effort(
            runtime_id="claude_code",
            profile=_family_profile(catalog=["claude-opus-5-5"]),
            env={},
        ).as_metadata()
        admitted["providerProfileId"] = "claude_anthropic_oauth"

        relaunch = resolve_model_effort(
            runtime_id="claude_code",
            profile=_family_profile(catalog=["claude-opus-5-5", "claude-opus-6"]),
            admitted_resolution=admitted,
            env={},
        )
        fresh = resolve_model_effort(
            runtime_id="claude_code",
            profile=_family_profile(catalog=["claude-opus-5-5", "claude-opus-6"]),
            env={},
        )

        assert relaunch.model == "claude-opus-5-5"
        assert relaunch.family_resolution_source == "admitted_selection"
        assert fresh.model == "claude-opus-6"

    def test_admitted_selection_from_another_profile_is_not_reused(self):
        admitted = {
            "providerProfileId": "other-profile",
            "effectiveModelTier": 2,
            "modelFamily": "opus",
            "modelMode": "automatic",
            "resolvedModel": "claude-opus-4",
        }
        resolved = resolve_model_effort(
            runtime_id="claude_code",
            profile=_family_profile(catalog=["claude-opus-6"]),
            admitted_resolution=admitted,
            env={},
        )
        assert resolved.model == "claude-opus-6"

    def test_custom_model_and_effort_do_not_consult_family_policy(self):
        resolved = resolve_model_effort(
            runtime_id="claude_code",
            profile=_family_profile(catalog=["claude-sonnet-5-5"]),
            authored_runtime={"model": "claude-opus-5", "effort": "high"},
            env={},
        )
        assert resolved.model == "claude-opus-5"
        assert resolved.effort == "high"
        assert resolved.model_source == "task_override"
        assert resolved.model_family is None

    def test_effort_override_keeps_the_family_model(self):
        resolved = resolve_model_effort(
            runtime_id="claude_code",
            profile=_family_profile(catalog=["claude-opus-5-5"]),
            requested_effort="high",
            env={},
        )
        assert resolved.model == "claude-opus-5-5"
        assert resolved.effort == "high"

    def test_runtime_default_null_model_tier_is_unchanged(self):
        resolved = resolve_model_effort(
            runtime_id="claude_code",
            profile=_family_profile(
                tiers=[
                    {
                        "label": "Runtime default",
                        "model": None,
                        "effort": None,
                        "parameters": {},
                        "annotations": {},
                    }
                ],
                default_model_tier=1,
            ),
            env={},
        )
        assert resolved.model_source == "runtime_default"
        assert resolved.model_family is None


class TestCodexNativeDefaultSuppression:
    @pytest.mark.parametrize("model_source", ["requested_tier", "profile_default_tier"])
    def test_tier_resolution_is_never_suppressed(self, model_source):
        from moonmind.workflows.executions.model_resolver import (
            codex_uses_native_default,
        )

        assert (
            codex_uses_native_default(
                suppress_default_model_flag=True,
                requested_model=None,
                resolved_model="gpt-6.1-sol",
                profile_default_model=None,
                model_source=model_source,
            )
            is False
        )

    def test_omitted_runtime_default_is_still_suppressed(self):
        from moonmind.workflows.executions.model_resolver import (
            codex_uses_native_default,
        )

        assert codex_uses_native_default(
            suppress_default_model_flag=True,
            requested_model=None,
            resolved_model="codex-MiniMax-M2.7",
            profile_default_model="codex-MiniMax-M2.7",
            model_source="provider_profile_default",
        )
