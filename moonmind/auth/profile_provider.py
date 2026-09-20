"""Provider-profile credential resolution without application-user identity.

Single-user design (#4349): execution credentials resolve from named provider
profiles and managed-secret references, never from a MoonMind ``User`` or
``UserProfile``. This provider therefore:

* resolves via an explicit ``profile_id`` against
  ``ManagedAgentProviderProfile.secret_refs`` + ``ManagedSecret`` rows;
* never creates profiles implicitly and never reads ``User``/``UserProfile``;
* blocks only the affected operation on missing/revoked/wrong-profile
  credentials (no fallback to another profile, account, model, or billing
  route);
* caches by ``(profile_id, key)`` with ``credential_generation`` /
  secret-revision invalidation instead of a user-keyed cache.

A deprecated ``user=`` keyword remains accepted for backward compatibility
with existing callers but is ignored for resolution: legacy ``UserProfile``
columns are migrated into managed secrets by
``api_service.services.profile_secret_migration`` and are no longer consulted
here. Passing only ``user`` without ``profile_id`` resolves to ``None``
instead of provisioning a profile.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .providers import AuthProvider
from .utils import RedactedSecret

logger = logging.getLogger(__name__)


def _normalize_key(key: str) -> str:
    return str(key or "").strip()


def _secret_refs_lookup(secret_refs: Any, key: str) -> str | None:
    if not isinstance(secret_refs, dict):
        return None
    normalized = _normalize_key(key)
    for candidate in (normalized, normalized.upper(), normalized.lower()):
        value = secret_refs.get(candidate)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class ProfileAuthProvider(AuthProvider):
    def __init__(self, db: AsyncSession, profile_svc: Any | None = None) -> None:
        self.db = db
        # Retained for constructor compatibility; no longer consulted.
        self.profile_svc = profile_svc
        # (profile_id, key) -> {"value": str, "generation": int,
        #   "credential_revision": int|None, "policy_revision": int|None,
        #   "slug": str}
        self._profile_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def invalidate(self, profile_id: str | None = None, key: str | None = None) -> None:
        """Drop cached entries for one profile/key or the whole cache."""
        if profile_id is None and key is None:
            self._profile_cache.clear()
            return
        for cache_key in [
            k
            for k in self._profile_cache
            if (profile_id is None or k[0] == profile_id)
            and (key is None or k[1] == _normalize_key(key))
        ]:
            self._profile_cache.pop(cache_key, None)

    async def get_secret(
        self,
        *,
        key: str,
        user: Any | None = None,
        profile_id: str | None = None,
        expected_credential_revision: int | None = None,
        expected_policy_revision: int | None = None,
        **kwargs: Any,
    ) -> str | None:
        # ``user`` is intentionally ignored: credential identity is the
        # profile + managed-secret reference, not application-user identity.
        # ``profile_id`` may also arrive via kwargs from older manager calls.
        resolved_profile_id = profile_id or kwargs.get("profile_id")
        if isinstance(resolved_profile_id, str):
            resolved_profile_id = resolved_profile_id.strip() or None
        if not resolved_profile_id:
            return None
        return await self.get_secret_for_profile(
            key=key,
            profile_id=resolved_profile_id,
            expected_credential_revision=expected_credential_revision,
            expected_policy_revision=expected_policy_revision,
        )

    async def get_secret_for_profile(
        self,
        *,
        key: str,
        profile_id: str,
        expected_credential_revision: int | None = None,
        expected_policy_revision: int | None = None,
    ) -> str | None:
        from api_service.db.models import ManagedAgentProviderProfile

        normalized_key = _normalize_key(key)
        pid = str(profile_id or "").strip()
        if not normalized_key or not pid:
            return None
        cache_key = (pid, normalized_key)
        cached = self._profile_cache.get(cache_key)

        try:
            profile = await self.db.get(ManagedAgentProviderProfile, pid)
        except Exception as exc:
            logging.warning("Profile lookup failed: %s", exc)
            return None
        if profile is None:
            # Unknown profile blocks without selecting another profile.
            self._profile_cache.pop(cache_key, None)
            return None
        if bool(getattr(profile, "enabled", True)) is False:
            self._profile_cache.pop(cache_key, None)
            return None

        generation = int(getattr(profile, "credential_generation", 1) or 1)
        ref = _secret_refs_lookup(getattr(profile, "secret_refs", None), normalized_key)
        if not ref:
            self._profile_cache.pop(cache_key, None)
            return None

        try:
            from moonmind.auth.secret_refs import SecretBackend, parse_secret_ref
        except Exception:
            return None
        try:
            parsed = parse_secret_ref(ref)
        except Exception:
            return None
        if parsed.backend != SecretBackend.DB_ENCRYPTED:
            # Only managed-secret references are profile-bound; other
            # backends are resolved by their owning resolvers, never by
            # falling back to a different profile here.
            return None
        slug = parsed.locator

        if cached is not None and cached.get("slug") == slug and cached.get("generation") == generation:
            # Validate cached entry against live revisions so rotation or
            # revocation invalidates even when the notification was lost.
            live = await self._read_active_secret(slug)
            if live is None:
                self._profile_cache.pop(cache_key, None)
                return None
            if (
                expected_credential_revision is not None
                and int(expected_credential_revision) != int(live["credential_revision"])
            ) or (
                expected_policy_revision is not None
                and int(expected_policy_revision) != int(live["policy_revision"])
            ):
                return None
            if (
                live["credential_revision"] == cached.get("credential_revision")
                and live["policy_revision"] == cached.get("policy_revision")
                and live["value"] == cached.get("value")
            ):
                return RedactedSecret(cached["value"])
            # Fall through to refresh the cache entry.

        live = await self._read_active_secret(slug)
        if live is None:
            self._profile_cache.pop(cache_key, None)
            return None
        if expected_credential_revision is not None and int(
            expected_credential_revision
        ) != int(live["credential_revision"]):
            return None
        if expected_policy_revision is not None and int(expected_policy_revision) != int(
            live["policy_revision"]
        ):
            return None
        self._profile_cache[cache_key] = {
            "value": live["value"],
            "generation": generation,
            "credential_revision": live["credential_revision"],
            "policy_revision": live["policy_revision"],
            "slug": slug,
        }
        return RedactedSecret(live["value"]) if live["value"] else None

    async def _read_active_secret(self, slug: str) -> dict[str, Any] | None:
        from api_service.db.models import ManagedSecret, SecretStatus

        try:
            result = await self.db.execute(
                select(ManagedSecret).where(
                    ManagedSecret.slug == slug,
                    ManagedSecret.status == SecretStatus.ACTIVE,
                )
            )
        except Exception as exc:
            logging.warning("Secret lookup failed: %s", exc)
            return None
        row = result.scalar_one_or_none()
        if row is None:
            return None
        value = str(row.ciphertext or "")
        if not value:
            return None
        return {
            "value": value,
            "credential_revision": int(getattr(row, "credential_revision", 1) or 1),
            "policy_revision": int(getattr(row, "policy_revision", 1) or 1),
        }
