"""Migrate legacy ``UserProfile``-held secrets into managed secrets (#4349).

Single-user design, sections 6 and 9: eligible legacy profile-held secret
values retain confidentiality and meaning when represented as managed-secret
references. This module owns that conversion for the four legacy encrypted
columns on ``UserProfile``:

* ``google_api_key_encrypted`` / ``openai_api_key_encrypted`` /
  ``github_token_encrypted`` / ``anthropic_api_key_encrypted``

Rules:

* Deterministic collision-safe slugs derived from existing IDs only:
  ``legacy-user-profile-{user_id_hex}-{field}`` where ``field`` is the
  short credential name (``google_api_key`` etc.). Distinct source
  credentials stay distinct even when their values match (no value-hash
  deduplication).
* Idempotent and retry/interruption safe: when the slug already exists with
  the same value, the existing row is reused; when it exists with a
  different value, the existing row wins and the conflict is reported
  without overwriting (no converted reference is published before its
  secret exists because this step creates secrets only — reference
  rewiring happens in the profile/secret consumers, not here).
* No second secret database: rows are written to ``managed_secrets`` with
  ``status=ACTIVE`` and existing encryption/wrapping material
  (``StringEncryptedType``).
* No plaintext in results, logs, or artifacts: returned items carry slugs,
  revisions, and redacted dispositions only.
* No permanent profile-to-user translation layer: slugs embed the source
  ``UserProfile.user_id`` solely as a stable deterministic identifier for
  the one-time conversion; resolution after migration uses profile IDs and
  managed-secret references.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

LEGACY_FIELDS: tuple[tuple[str, str], ...] = (
    ("google_api_key_encrypted", "google_api_key"),
    ("openai_api_key_encrypted", "openai_api_key"),
    ("github_token_encrypted", "github_token"),
    ("anthropic_api_key_encrypted", "anthropic_api_key"),
)

LEGACY_SLUG_PREFIX = "legacy-user-profile-"


def legacy_secret_slug(user_id: UUID | str, field: str) -> str:
    """Return the deterministic slug for one legacy (user, field) credential."""
    if isinstance(user_id, UUID):
        user_hex = user_id.hex
    else:
        user_hex = str(user_id).strip().lower().replace("-", "")
        if not user_hex:
            raise ValueError("user_id is required for legacy secret slugs")
    short = str(field or "").strip().lower()
    if not short:
        raise ValueError("field is required for legacy secret slugs")
    return f"{LEGACY_SLUG_PREFIX}{user_hex}-{short}"


def _is_set(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


async def migrate_legacy_profile_secrets(
    session: AsyncSession,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Copy eligible legacy ``UserProfile`` secrets into ``ManagedSecret`` rows.

    Returns a metadata-only summary: ``{"migrated": N, "created": M,
    "reused": K, "items": [{"slug": ..., "field": ...,
    "credential_revision": ..., "policy_revision": ...,
    "outcome": "created"|"reused"|"conflict_kept_existing"}]}``.
    Never returns plaintext values.
    """
    from api_service.db.models import ManagedSecret, SecretStatus, UserProfile

    result_items: list[dict[str, Any]] = []
    created = 0
    reused = 0

    rows = (await session.execute(select(UserProfile))).scalars().all()
    for profile in rows:
        user_id = getattr(profile, "user_id", None)
        if user_id is None:
            continue
        for column, short in LEGACY_FIELDS:
            try:
                value = getattr(profile, column, None)
            except Exception:
                continue
            if not _is_set(value):
                continue
            plaintext = str(value)
            slug = legacy_secret_slug(user_id, short)
            existing = (
                await session.execute(
                    select(ManagedSecret).where(ManagedSecret.slug == slug)
                )
            ).scalar_one_or_none()
            if existing is not None:
                # Secret must already exist before any reference is published:
                # reuse the winner; never overwrite a converted reference.
                try:
                    same = str(existing.ciphertext or "") == plaintext
                except Exception:
                    same = False
                if same:
                    reused += 1
                    outcome = "reused"
                else:
                    # Distinct source credential already converted under this
                    # slug with a different value: keep the existing secret
                    # (first-writer wins) and report without plaintext.
                    logger.warning(
                        "legacy profile secret conflict keeps existing slug=%s field=%s",
                        slug,
                        short,
                    )
                    outcome = "conflict_kept_existing"
                result_items.append(
                    {
                        "slug": slug,
                        "field": short,
                        "credential_revision": int(
                            getattr(existing, "credential_revision", 1) or 1
                        ),
                        "policy_revision": int(
                            getattr(existing, "policy_revision", 1) or 1
                        ),
                        "outcome": outcome,
                    }
                )
                continue
            # Create-then-reference ordering: the secret row is flushed
            # before any caller publishes a reference to it. Each insert
            # uses a savepoint so a concurrent-winner conflict rolls back
            # only that row, preserving earlier conversions in this run.
            row = ManagedSecret(
                slug=slug,
                ciphertext=plaintext,
                status=SecretStatus.ACTIVE,
                details={
                    "migrated_from": "user_profile",
                    "legacy_field": column,
                    "issue": 4349,
                },
            )
            try:
                async with session.begin_nested():
                    session.add(row)
                    await session.flush()
            except IntegrityError:
                # Concurrent migrator won the race: converge on the winner.
                winner = (
                    await session.execute(
                        select(ManagedSecret).where(ManagedSecret.slug == slug)
                    )
                ).scalar_one_or_none()
                if winner is None:
                    raise
                reused += 1
                result_items.append(
                    {
                        "slug": slug,
                        "field": short,
                        "credential_revision": int(
                            getattr(winner, "credential_revision", 1) or 1
                        ),
                        "policy_revision": int(
                            getattr(winner, "policy_revision", 1) or 1
                        ),
                        "outcome": "reused",
                    }
                )
                continue
            created += 1
            result_items.append(
                {
                    "slug": slug,
                    "field": short,
                    "credential_revision": int(
                        getattr(row, "credential_revision", 1) or 1
                    ),
                    "policy_revision": int(getattr(row, "policy_revision", 1) or 1),
                    "outcome": "created",
                }
            )

    if commit:
        await session.commit()
    else:
        await session.flush()
    return {
        "migrated": len(result_items),
        "created": created,
        "reused": reused,
        "items": result_items,
    }
