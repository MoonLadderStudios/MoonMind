"""Tests for the pure Codex OAuth capacity policy."""

from enum import Enum

import pytest

from moonmind.provider_profiles.oauth_policy import (
    effective_oauth_capacity_for_finalization,
    is_codex_oauth_profile,
    validate_codex_oauth_capacity,
)


class _Value(str, Enum):
    CODEX = "codex_cli"
    OAUTH = "oauth_volume"
    HOME = "oauth_home"


@pytest.mark.parametrize("enum_shaped", [False, True])
def test_codex_oauth_identity_normalizes_strings_and_enums(enum_shaped: bool) -> None:
    assert is_codex_oauth_profile(
        runtime_id=_Value.CODEX if enum_shaped else "codex_cli",
        credential_source=_Value.OAUTH if enum_shaped else "oauth_volume",
        materialization_mode=_Value.HOME if enum_shaped else "oauth_home",
    )


def test_codex_oauth_capacity_rejects_new_parallel_writes() -> None:
    with pytest.raises(ValueError, match="require max_parallel_runs=1"):
        validate_codex_oauth_capacity(
            runtime_id="codex_cli",
            credential_source="oauth_volume",
            materialization_mode="oauth_home",
            max_parallel_runs=3,
        )


def test_finalization_repairs_only_codex_oauth_capacity() -> None:
    assert effective_oauth_capacity_for_finalization(
        runtime_id="codex_cli", requested_capacity=3
    ) == 1
    assert effective_oauth_capacity_for_finalization(
        runtime_id="claude_code", requested_capacity=3
    ) == 3


@pytest.mark.parametrize("value", [None, "bad", 0, -1, True])
def test_finalization_rejects_malformed_capacity(value: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        effective_oauth_capacity_for_finalization(
            runtime_id="claude_code", requested_capacity=value
        )
