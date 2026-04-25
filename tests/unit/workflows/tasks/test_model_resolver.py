"""Unit tests for the canonical model-resolution helper.

Tests the three-level precedence:
  1. task_override  (explicit requested model)
  2. provider_profile_default  (profile.default_model)
  3. runtime_default  (canonical registry)
  4. none  (nothing available)

Also tests that `normalize_runtime_id` applies aliases correctly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from moonmind.workflows.tasks.model_resolver import resolve_effective_model
from moonmind.workflows.tasks.runtime_defaults import normalize_runtime_id

# ---------------------------------------------------------------------------
# normalize_runtime_id
# ---------------------------------------------------------------------------

class TestNormalizeRuntimeId:
    def test_codex_cli_passthrough(self):
        assert normalize_runtime_id("codex_cli") == "codex_cli"

    def test_gemini_cli_passthrough(self):
        assert normalize_runtime_id("gemini_cli") == "gemini_cli"

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
        assert normalize_runtime_id("Gemini_CLI") == "gemini_cli"

    def test_unknown_runtime_passthrough(self):
        assert normalize_runtime_id("some_future_runtime") == "some_future_runtime"

    def test_none_falls_back_to_default(self):
        # None / empty falls back to DEFAULT_TASK_RUNTIME
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
            ("codex_cli", "gpt-5.4"),
            ("gemini_cli", "gemini-3.1-pro-preview"),
            ("claude_code", "claude-opus-4-7"),
        ],
    )
    def test_runtime_default_for_each_canonical_id(self, runtime_id, expected_model):
        model, source = resolve_effective_model(
            runtime_id=runtime_id,
            profile=None,
            requested_model=None,
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

    def test_no_runtime_resolves_to_default_task_runtime(self):
        model, source = resolve_effective_model(
            runtime_id=None,
            profile=None,
            requested_model=None,
        )
        assert model == "gpt-5.4"
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
        )
        assert model == "gpt-5.4"
        assert source == "runtime_default"
