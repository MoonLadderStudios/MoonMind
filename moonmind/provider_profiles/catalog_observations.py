"""Record a Provider Profile's observed native model catalog.

Codex and Claude Code profiles have no separate discovery probe. Every
managed launch already asks the profile's exact host for its model options
before the session starts, so that answer is persisted as the profile's
catalog observation. Automatic family tiers resolve against it for later
admissions, and the tier editor shows it.

Only ``model_catalog_evidence_json`` is written, and only when the credential
generation the observation was taken under is still current. Authored tier
policy is never touched, and an empty or failed observation leaves the last
usable one in place. OpenCode profiles keep their pinned-runtime validation
owner (:mod:`moonmind.omnigent.bootstrap.provider_revalidation`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Mapping

logger = logging.getLogger(__name__)

__all__ = [
    "OBSERVED_RUNTIME_IDS",
    "catalog_evidence_from_model_options",
    "record_profile_catalog_observation",
]

OBSERVED_RUNTIME_IDS = frozenset({"codex_cli", "claude_code"})
EVIDENCE_SOURCE = "exact_host_model_options"

_MAX_ROWS = 256
_MAX_TEXT = 255


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text and len(text) <= _MAX_TEXT else None


def _efforts(row: Mapping[str, Any]) -> list[str] | None:
    for key in ("supportedReasoningEfforts", "supportedEfforts", "supported_efforts"):
        raw = row.get(key)
        if not isinstance(raw, list):
            continue
        efforts = []
        for item in raw[:16]:
            if isinstance(item, Mapping):
                item = item.get("reasoningEffort") or item.get("value")
            text = _text(item)
            if text and text not in efforts:
                efforts.append(text)
        return efforts
    return None


def catalog_evidence_from_model_options(
    model_options: Mapping[str, Any],
    *,
    credential_generation: int | None,
    observed_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Return bounded, secret-free catalog evidence, or None when empty.

    Rows keep the concrete model (a Claude picker alias row reports its
    resolution in ``model``) plus the lifecycle and effort metadata the
    family resolver uses.
    """

    raw_rows = model_options.get("models")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rows[:_MAX_ROWS] if isinstance(raw_rows, list) else []:
        if not isinstance(raw, Mapping):
            continue
        model_id = _text(raw.get("model")) or _text(raw.get("id"))
        if model_id is None or model_id in seen:
            continue
        seen.add(model_id)
        row: dict[str, Any] = {"qualifiedId": model_id}
        label = _text(raw.get("displayName") or raw.get("label"))
        if label:
            row["label"] = label
        for key in ("status", "family", "line"):
            if text := _text(raw.get(key)):
                row[key] = text
        for key in ("hidden", "disabled"):
            if raw.get(key) is True:
                row[key] = True
        efforts = _efforts(raw)
        if efforts is not None:
            row["supportedEfforts"] = efforts
        rows.append(row)
    routable = model_options.get("routable_models")
    for raw in routable[:_MAX_ROWS] if isinstance(routable, list) else []:
        model_id = _text(raw)
        if model_id is not None and model_id not in seen:
            seen.add(model_id)
            rows.append({"qualifiedId": model_id})
    if not rows:
        return None
    return {
        "source": EVIDENCE_SOURCE,
        "credentialGeneration": credential_generation,
        "validatedAt": (observed_at or datetime.now(UTC)).isoformat(),
        "models": rows[:_MAX_ROWS],
    }


async def record_profile_catalog_observation(
    session_factory: Any,
    *,
    profile_id: str,
    credential_generation: int | None,
    model_options: Mapping[str, Any],
    observed_at: datetime | None = None,
) -> bool:
    """Persist one exact-host observation for a Codex or Claude Code profile.

    Returns True when evidence was written. Failures are logged and swallowed:
    an observation is advisory and must never fail the launch that took it.
    """

    from sqlalchemy import update

    from api_service.db.models import ManagedAgentProviderProfile

    try:
        evidence = catalog_evidence_from_model_options(
            model_options,
            credential_generation=credential_generation,
            observed_at=observed_at,
        )
        if evidence is None:
            return False
        statement = (
            update(ManagedAgentProviderProfile)
            .where(ManagedAgentProviderProfile.profile_id == profile_id)
            .where(ManagedAgentProviderProfile.runtime_id.in_(OBSERVED_RUNTIME_IDS))
            .values(
                model_catalog_evidence_json=evidence,
                # An observation is not a policy edit: keep the profile
                # version that previews and editors compare against.
                updated_at=ManagedAgentProviderProfile.updated_at,
            )
        )
        if credential_generation is not None:
            # An observation taken under a replaced credential does not
            # describe the current account.
            statement = statement.where(
                ManagedAgentProviderProfile.credential_generation
                == credential_generation
            )
        async with session_factory() as session:
            result = await session.execute(statement)
            await session.commit()
        return bool(result.rowcount)
    except Exception:
        logger.warning(
            "provider_profile_catalog_observation_failed profile_id=%s",
            profile_id,
            exc_info=True,
        )
        return False
