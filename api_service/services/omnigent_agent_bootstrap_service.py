"""Durable bootstrap default for Omnigent agent selection (MoonLadderStudios/MoonMind#3517).

Section 8 of MoonLadderStudios/MoonMind#3517 requires migrating the
``OMNIGENT_DEFAULT_AGENT_NAME`` environment default and its single-default
behavior into an explicit, durable, seeded bootstrap agent profile. The
environment value is retained only as a bootstrap/local-development fallback,
its use is recorded, durable state wins, and conflicts fail closed.

This module owns two side-effect-free-by-default primitives:

* :func:`resolve_default_agent_selection` — the authority that prefers the
  durable active default profile and records env-fallback use; and
* :func:`seed_bootstrap_agent_profile` — startup materialization of the env
  default as an explicit draft bootstrap profile version when no durable
  agent-profile state exists.

The seeded bootstrap profile is a **draft**: activation requires bridge-backed
validation evidence that cannot be produced offline, so an operator promotes
the materialized version once the endpoint is reachable. Until an active
default profile exists, the recorded environment fallback governs selection.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    OmnigentAgentProfile,
    OmnigentAgentProfileAuditEvent,
    OmnigentAgentProfileVersion,
)

logger = logging.getLogger(__name__)

BOOTSTRAP_PROFILE_ID = "omnigent-bootstrap-default"
_ENV_DEFAULT_AGENT_NAME = "OMNIGENT_DEFAULT_AGENT_NAME"

# Canonical Codex-via-Omnigent defaults for the materialized bootstrap version.
# These mirror the built-in ``omnigent-codex@1`` execution path and carry no
# runtime authority (no credentials, host paths, or launch settings).
_BOOTSTRAP_ENDPOINT_REF = "default"
_BOOTSTRAP_BRIDGE_MODE = "proxy"
_BOOTSTRAP_HARNESS = "codex-native"
_BOOTSTRAP_EXECUTION_PROFILE_REF = "omnigent-codex@1"
_BOOTSTRAP_LAUNCH_POLICY_REF = "codex-on-demand@1"
_BOOTSTRAP_PROVIDER_RUNTIME_ID = "codex_cli"
_BOOTSTRAP_PROVIDER_CREDENTIAL_SOURCE = "oauth_volume"
_BOOTSTRAP_PROVIDER_MATERIALIZATION_MODE = "oauth_home"
_BOOTSTRAP_REQUIRED_CAPABILITIES = ["session.start"]


class BootstrapDefaultConflictError(RuntimeError):
    """A durable default profile exists but is not in a launch-ready state.

    Sec 8 requires conflicts to fail closed rather than silently falling back
    to the environment value.
    """


@dataclass(frozen=True, slots=True)
class DefaultAgentResolution:
    """Resolved default Omnigent agent selection and its provenance."""

    source: str  # "durable_profile" | "env_fallback" | "none"
    agent_id: str | None = None
    agent_name: str | None = None
    profile_id: str | None = None
    version: int | None = None
    used_env_fallback: bool = False

    @property
    def default_agent_name(self) -> str:
        """Name fed into the name-based fallback resolution slot."""

        return (self.agent_name or "").strip()


def _clean(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _env_default_agent_name(env: Mapping[str, Any] | None) -> str:
    source = env if env is not None else os.environ
    return _clean(source.get(_ENV_DEFAULT_AGENT_NAME))


def _digest(document: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_bootstrap_document(agent_name: str) -> dict[str, Any]:
    """Build the normalized bootstrap profile document for an env agent name.

    The environment provides only a stable upstream agent selector; the
    remaining fields use the canonical Codex-via-Omnigent defaults so the
    materialized draft is a coherent starting point an operator can validate
    and promote. The result is the fully-normalized document form (identical
    to what the profile API persists for an equivalent create) so digests
    stay consistent across the system without coupling to the router models.
    """

    return {
        "bridgeMode": _BOOTSTRAP_BRIDGE_MODE,
        "capture": {"evidence": True, "stream": True},
        "continuations": {"branch": True, "checkpoint": True, "remediation": True},
        "endpointRef": _BOOTSTRAP_ENDPOINT_REF,
        "execution": {
            "allowedLaunchPolicyRefs": [_BOOTSTRAP_LAUNCH_POLICY_REF],
            "defaultExecutionProfileRef": _BOOTSTRAP_EXECUTION_PROFILE_REF,
        },
        "harness": _BOOTSTRAP_HARNESS,
        "model": {"settings": {}},
        "policyRef": _BOOTSTRAP_LAUNCH_POLICY_REF,
        "providerRequirements": {
            "credentialSource": _BOOTSTRAP_PROVIDER_CREDENTIAL_SOURCE,
            "materializationMode": _BOOTSTRAP_PROVIDER_MATERIALIZATION_MODE,
            "providerIds": [],
            "runtimeId": _BOOTSTRAP_PROVIDER_RUNTIME_ID,
        },
        "publish": {"mode": "none"},
        "rag": {"followUp": {}, "initial": {}},
        "requiredCapabilities": list(_BOOTSTRAP_REQUIRED_CAPABILITIES),
        "schemaVersion": "moonmind.omnigent-agent-profile.v1",
        "skills": [],
        "source": {"upstreamId": agent_name},
        "tools": [],
        "workspace": {"mutation": "allowed", "requiredCapabilities": []},
    }


async def _load_active_version(
    session: AsyncSession, profile: OmnigentAgentProfile
) -> OmnigentAgentProfileVersion | None:
    if profile.active_version is None:
        return None
    return await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == profile.profile_id,
            OmnigentAgentProfileVersion.version == profile.active_version,
        )
    )


async def resolve_default_agent_selection(
    session: AsyncSession, *, env: Mapping[str, Any] | None = None
) -> DefaultAgentResolution:
    """Resolve the default Omnigent agent selection, durable state first.

    * A durable active profile marked ``default_for_runtime`` wins and yields
      its stable upstream identity.
    * A profile marked default but not active/validated is a conflict and
      fails closed.
    * Otherwise the ``OMNIGENT_DEFAULT_AGENT_NAME`` environment value is used
      as a bootstrap/local-development fallback and its use is recorded.
    """

    default_profile = await session.scalar(
        select(OmnigentAgentProfile).where(
            OmnigentAgentProfile.default_for_runtime.is_(True)
        )
    )
    if default_profile is not None:
        if default_profile.state != "active" or default_profile.active_version is None:
            raise BootstrapDefaultConflictError(
                "default Omnigent agent profile "
                f"'{default_profile.profile_id}' is marked default but is not "
                "an active validated version"
            )
        version = await _load_active_version(session, default_profile)
        if version is None:
            raise BootstrapDefaultConflictError(
                "default Omnigent agent profile "
                f"'{default_profile.profile_id}' has no resolvable active version"
            )
        document = version.document or {}
        source = document.get("source", {}) if isinstance(document, dict) else {}
        snapshot = version.upstream_snapshot or {}
        bundle_import = (version.rollout_metadata or {}).get("bundleImport") or {}
        imported_agent = bundle_import.get("upstreamAgent") or {}
        agent_id = _clean(source.get("upstreamId")) or _clean(imported_agent.get("id")) or None
        if not agent_id:
            raise BootstrapDefaultConflictError(
                f"default Omnigent agent profile '{default_profile.profile_id}' "
                "has no stable launch agent identity"
            )
        agent_name = agent_id
        return DefaultAgentResolution(
            source="durable_profile",
            agent_id=agent_id,
            agent_name=agent_name,
            profile_id=default_profile.profile_id,
            version=version.version,
            used_env_fallback=False,
        )

    env_name = _env_default_agent_name(env)
    if env_name:
        logger.info(
            "Omnigent default agent resolved from %s bootstrap fallback; "
            "no durable active default profile is present",
            _ENV_DEFAULT_AGENT_NAME,
        )
        return DefaultAgentResolution(
            source="env_fallback",
            agent_name=env_name,
            used_env_fallback=True,
        )

    return DefaultAgentResolution(source="none")


async def seed_bootstrap_agent_profile(
    session: AsyncSession,
    *,
    env: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> str | None:
    """Materialize the env default as a draft bootstrap profile when absent.

    Idempotent and fail-closed: skips when any durable agent-profile state
    already exists (durable state wins) or when no environment default is
    configured. Returns the seeded profile id, or ``None`` when nothing was
    materialized.
    """

    env_name = _env_default_agent_name(env)
    if not env_name:
        return None

    existing_count = int(
        await session.scalar(select(func.count()).select_from(OmnigentAgentProfile))
        or 0
    )
    if existing_count:
        # Durable state already exists; never overwrite it from the env value.
        return None

    observed_at = now or datetime.now(timezone.utc)
    document = build_bootstrap_document(env_name)
    profile = OmnigentAgentProfile(
        profile_id=BOOTSTRAP_PROFILE_ID,
        display_name="Bootstrap Omnigent Default",
        description=(
            "Materialized from the OMNIGENT_DEFAULT_AGENT_NAME environment "
            "default. Validate and activate this version to make it the "
            "durable runtime default."
        ),
        owner_id=None,
        visibility="workspace",
        state="draft",
    )
    version = OmnigentAgentProfileVersion(
        profile_id=BOOTSTRAP_PROFILE_ID,
        version=1,
        digest=_digest(document),
        document=document,
        created_by=None,
        rollout_metadata={
            "origin": "env_bootstrap",
            "envVar": _ENV_DEFAULT_AGENT_NAME,
            "materializedAt": observed_at.isoformat(),
        },
    )
    audit = OmnigentAgentProfileAuditEvent(
        profile_id=BOOTSTRAP_PROFILE_ID,
        action="bootstrap_materialized",
        version=1,
        actor_id=None,
        metadata_json={"origin": "env_bootstrap", "envVar": _ENV_DEFAULT_AGENT_NAME},
    )
    session.add_all([profile, version, audit])
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent startup already seeded the bootstrap profile.
        await session.rollback()
        return None
    logger.info(
        "Materialized Omnigent bootstrap agent profile '%s' from %s",
        BOOTSTRAP_PROFILE_ID,
        _ENV_DEFAULT_AGENT_NAME,
    )
    return BOOTSTRAP_PROFILE_ID


__all__ = [
    "BOOTSTRAP_PROFILE_ID",
    "BootstrapDefaultConflictError",
    "DefaultAgentResolution",
    "build_bootstrap_document",
    "resolve_default_agent_selection",
    "seed_bootstrap_agent_profile",
]
