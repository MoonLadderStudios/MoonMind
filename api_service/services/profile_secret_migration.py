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
  hyphenated credential name (``openai-api-key`` etc., a valid ``db://``
  locator). Distinct source credentials stay distinct even when their
  values match (no value-hash deduplication).
* Idempotent and retry/interruption safe: when the slug already exists with
  the same value, the existing row is reused; when it exists with a
  different value, the existing row wins and the conflict is reported
  without overwriting (no converted reference is published before its
  secret exists: this step creates secrets first, and
  ``rewire_provider_profile_secret_refs`` verifies each secret exists
  ACTIVE before publishing its reference).
* No second secret database: rows are written to ``managed_secrets`` with
  ``status=ACTIVE`` and existing encryption/wrapping material
  (``StringEncryptedType``).
* No plaintext in results, logs, or artifacts: returned items carry slugs,
  revisions, and redacted dispositions only.
* No permanent profile-to-user translation layer: slugs embed the source
  ``UserProfile.user_id`` solely as a stable deterministic identifier for
  the one-time conversion; resolution after migration uses profile IDs and
  managed-secret references.
* Transactional reference rewiring: ``rewire_provider_profile_secret_refs``
  publishes ``db://`` references onto an existing
  ``ManagedAgentProviderProfile`` only after verifying every referenced
  ``ManagedSecret`` exists with ``status=ACTIVE``, in a single transaction
  that also bumps ``credential_generation`` when bindings change (so
  caches and host leases invalidate). Retry/interruption safe and
  idempotent: re-applying identical refs is a no-op success.
* Single-operator eligibility (design section 9):
  ``check_single_operator_eligibility`` blocks conversion when legacy
  secret-bearing rows are attributable to more than one operator. It
  never chooses an owner, merges people, or discards rows. The gate is
  enforced by default in ``migrate_legacy_profile_secrets`` (raising
  ``MultiOperatorAttributionError`` before any write) and by the
  production orchestrator
  ``run_single_operator_profile_secret_conversion`` (eligibility, then
  migrate, then optional transactional rewires), which is also invoked
  from application startup.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class MultiOperatorAttributionError(ValueError):
    """Legacy profile-held secrets span more than one operator.

    The single-operator conversion path must not choose an owner, merge
    people, or silently discard rows. Cross-operator attribution stays owned
    by #4346's guarded migration; this conversion converts nothing when
    raised before any write.
    """

LEGACY_FIELDS: tuple[tuple[str, str], ...] = (
    ("google_api_key_encrypted", "google_api_key"),
    ("openai_api_key_encrypted", "openai_api_key"),
    ("github_token_encrypted", "github_token"),
    ("anthropic_api_key_encrypted", "anthropic_api_key"),
)

LEGACY_SLUG_PREFIX = "legacy-user-profile-"


def legacy_secret_slug(user_id: UUID | str, field: str) -> str:
    """Return the deterministic slug for one legacy (user, field) credential.

    The slug is a valid ``db://`` locator (lowercase alphanumerics and
    hyphens only) so migrated secrets are immediately referenceable:
    ``legacy-user-profile-{user_id_hex}-{field}`` where ``field`` is the
    hyphenated credential name (``openai-api-key`` etc.).
    """
    if isinstance(user_id, UUID):
        user_hex = user_id.hex
    else:
        user_hex = str(user_id).strip().lower().replace("-", "")
        if not user_hex:
            raise ValueError("user_id is required for legacy secret slugs")
    short = str(field or "").strip().lower().replace("_", "-")
    if not short:
        raise ValueError("field is required for legacy secret slugs")
    return f"{LEGACY_SLUG_PREFIX}{user_hex}-{short}"


def _is_set(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


async def migrate_legacy_profile_secrets(
    session: AsyncSession,
    *,
    commit: bool = True,
    enforce_eligibility: bool = True,
) -> dict[str, Any]:
    """Copy eligible legacy ``UserProfile`` secrets into ``ManagedSecret`` rows.

    When ``enforce_eligibility`` is true (default), the single-operator
    eligibility gate runs first and multi-operator databases raise
    :class:`MultiOperatorAttributionError` before any write. Pass
    ``enforce_eligibility=False`` only from an owning guarded migration
    (e.g. #4346's full-dataset path) that already resolved attribution;
    the bypass is logged as a warning.

    Returns a metadata-only summary: ``{"migrated": N, "created": M,
    "reused": K, "items": [{"slug": ..., "field": ...,
    "credential_revision": ..., "policy_revision": ...,
    "outcome": "created"|"reused"|"conflict_kept_existing"}]}``.
    Never returns plaintext values.
    """
    from api_service.db.models import ManagedSecret, SecretStatus, UserProfile

    if enforce_eligibility:
        eligibility = await check_single_operator_eligibility(session)
        if not eligibility.get("eligible", False):
            logger.warning(
                "legacy profile secret conversion blocked: %s",
                eligibility.get("reason", "ineligible"),
            )
            raise MultiOperatorAttributionError(
                "legacy profile-held secrets are attributable to "
                f"{eligibility.get('secret_holders', '?')} operators "
                f"({eligibility.get('reason', 'ineligible')}); refusing to "
                "choose an owner, merge people, or discard rows"
            )
    else:
        logger.warning(
            "legacy profile secret conversion running with eligibility "
            "enforcement bypassed by the owning caller"
        )

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


async def check_single_operator_eligibility(session: AsyncSession) -> dict[str, Any]:
    """Decide whether legacy profile-held secrets are convertible (design §9).

    Eligible when every ``UserProfile`` row carrying a legacy secret value is
    attributable to at most one operator (zero rows = fresh database =
    eligible). Blocked when secret-bearing rows span multiple ``user_id``
    values: the migration must not choose an owner, merge people, or
    silently discard rows. Returns metadata only (counts + hex user ids),
    never secret values.
    """
    from api_service.db.models import UserProfile

    conditions = [
        UserProfile.google_api_key_encrypted.is_not(None),
        UserProfile.openai_api_key_encrypted.is_not(None),
        UserProfile.github_token_encrypted.is_not(None),
        UserProfile.anthropic_api_key_encrypted.is_not(None),
    ]
    rows = (
        await session.execute(
            select(UserProfile.user_id).where(or_(*conditions))
        )
    ).all()
    user_ids = sorted({str(r[0]) for r in rows if r[0] is not None})
    # Exclude rows whose "values" are empty strings: presence check above is
    # NULL-based, so confirm at least one non-blank value exists.
    if user_ids:
        profiles = (
            await session.execute(select(UserProfile).where(or_(*conditions)))
        ).scalars().all()
        populated = sorted(
            {
                str(p.user_id)
                for p in profiles
                if p.user_id is not None
                and any(
                    _is_set(getattr(p, column, None)) for column, _ in LEGACY_FIELDS
                )
            }
        )
        user_ids = populated
    if len(user_ids) <= 1:
        return {
            "eligible": True,
            "reason": "single_operator_or_empty",
            "secret_holders": len(user_ids),
            "user_ids": user_ids,
        }
    logger.warning(
        "legacy profile secret conversion blocked: %d distinct operators",
        len(user_ids),
    )
    return {
        "eligible": False,
        "reason": "multi_operator_attribution",
        "secret_holders": len(user_ids),
        "user_ids": user_ids,
    }


async def rewire_provider_profile_secret_refs(
    session: AsyncSession,
    *,
    profile_id: str,
    refs: Mapping[str, str],
    commit: bool = True,
) -> dict[str, Any]:
    """Transactionally point a provider profile at migrated ``db://`` secrets.

    ``refs`` maps secret keys (e.g. ``{"OPENAI_API_KEY": "db://slug"}``) to
    managed-secret references. Every referenced slug must already exist with
    ``status=ACTIVE`` — no converted reference is published before its
    secret exists. The profile row must already exist (existing IDs only;
    never created here). On an actual binding change,
    ``credential_generation`` is bumped by one so per-profile caches and
    host leases invalidate; re-applying identical refs is a no-op that
    leaves the generation untouched (retry/interruption safe). Returns
    metadata only: profile id, applied refs, generation, and outcome.
    """
    from api_service.db.models import (
        ManagedAgentProviderProfile,
        ManagedSecret,
        SecretStatus,
    )
    from moonmind.auth.secret_refs import SecretBackend, parse_secret_ref

    pid = str(profile_id or "").strip()
    if not pid:
        raise ValueError("profile_id is required")
    if not isinstance(refs, Mapping) or not refs:
        raise ValueError("refs must be a non-empty mapping")
    normalized: dict[str, str] = {}
    for key, ref in refs.items():
        norm_key = str(key or "").strip()
        if not norm_key:
            raise ValueError("secret key is required")
        parsed = parse_secret_ref(str(ref or ""))
        if parsed.backend != SecretBackend.DB_ENCRYPTED:
            raise ValueError(f"secret ref for {norm_key!r} must use db:// backend")
        normalized[norm_key] = parsed.normalized_ref
    slugs = sorted({r.split("://", 1)[1] for r in normalized.values()})
    existing = (
        await session.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug.in_(slugs),
                ManagedSecret.status == SecretStatus.ACTIVE,
            )
        )
    ).scalars().all()
    by_slug = {row.slug: row for row in existing}
    missing = [s for s in slugs if s not in by_slug]
    if missing:
        raise ValueError(
            "referenced managed secret(s) missing or not ACTIVE: "
            + ", ".join(missing)
        )
    profile = await session.get(ManagedAgentProviderProfile, pid)
    if profile is None:
        raise ValueError(f"provider profile {pid!r} does not exist")
    current = dict(getattr(profile, "secret_refs", None) or {})
    merged = {**current, **normalized}
    changed = any(current.get(k) != v for k, v in normalized.items())
    outcome = "reused" if not changed else "rewired"
    generation_before = int(getattr(profile, "credential_generation", 1) or 1)
    if changed:
        profile.secret_refs = merged
        profile.credential_generation = generation_before + 1
        await session.flush()
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(profile)
    return {
        "profile_id": pid,
        "applied_refs": normalized,
        "outcome": outcome,
        "credential_generation": int(
            getattr(profile, "credential_generation", generation_before) or 1
        ),
        "credential_generation_before": generation_before,
    }


async def run_single_operator_profile_secret_conversion(
    session: AsyncSession,
    *,
    profile_rewires: Mapping[str, Mapping[str, str]] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Eligibility-gated conversion entrypoint for the #4349 scope.

    This is the production orchestrator for legacy profile-held secret
    conversion: it checks single-operator eligibility first (read-only, no
    mutation on block), then migrates eligible secrets into managed secrets,
    then optionally rewires provider profiles onto the converted references.
    Rewiring publishes a ``db://`` reference only after verifying its secret
    exists ACTIVE, so no converted reference is published before its secret
    exists. Returns metadata only, never plaintext values.

    Full-dataset (settings/presets/artifacts/schedules) attribution remains
    owned by #4346's guarded migration; this entrypoint covers only the
    #4349 conversion scope.
    """
    eligibility = await check_single_operator_eligibility(session)
    if not eligibility.get("eligible", False):
        logger.warning(
            "single-operator profile secret conversion blocked: %s",
            eligibility.get("reason", "ineligible"),
        )
        raise MultiOperatorAttributionError(
            "legacy profile-held secrets are attributable to "
            f"{eligibility.get('secret_holders', '?')} operators "
            f"({eligibility.get('reason', 'ineligible')}); refusing to "
            "choose an owner, merge people, or discard rows"
        )
    migration = await migrate_legacy_profile_secrets(
        session, commit=False, enforce_eligibility=False
    )
    rewires: list[dict[str, Any]] = []
    for profile_id, refs in (profile_rewires or {}).items():
        rewires.append(
            await rewire_provider_profile_secret_refs(
                session, profile_id=str(profile_id), refs=refs, commit=False
            )
        )
    if commit:
        await session.commit()
    else:
        await session.flush()
    return {
        "eligibility": eligibility,
        "migration": migration,
        "rewires": rewires,
    }
