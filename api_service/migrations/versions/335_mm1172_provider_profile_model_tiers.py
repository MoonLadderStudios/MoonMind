"""Compatibility shim for duplicated provider profile model-tiers migration.

The `335_mm1172_provider_tiers` migration was introduced during a merge where
`335_provider_profile_model_tiers` already provided the canonical schema and data
migration for `model_tiers`. Keeping this revision as a no-op shim preserves
historical revision stamps while eliminating the duplicate head.

Revision ID: 335_mm1172_provider_tiers
Revises: 335_provider_profile_model_tiers
Create Date: 2026-07-10
"""

from __future__ import annotations

from typing import Union

revision: str = "335_mm1172_provider_tiers"
down_revision: Union[str, None] = "335_provider_profile_model_tiers"

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]


def upgrade() -> None:
    # This migration is intentionally a compatibility shim; all schema changes now
    # live in `335_provider_profile_model_tiers`.
    return


def downgrade() -> None:
    # No-op compatibility revision.
    return
