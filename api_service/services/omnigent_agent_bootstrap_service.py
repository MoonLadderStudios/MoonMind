"""Durable bootstrap default for Omnigent agent selection (MoonLadderStudios/MoonMind#3517).

The normal local deployment has one stock portable Codex agent identity,
``codex-native-ui``. MoonMind materializes its stable upstream ID and version as
an explicit, durable, active bootstrap profile. ``OMNIGENT_DEFAULT_AGENT_NAME``
remains an optional first-start override; durable state wins after
materialization and conflicts fail closed.

This module owns three side-effect-free-by-default primitives:

* :func:`resolve_default_agent_selection` — the authority that prefers the
  durable active default profile and records env-fallback use;
* :func:`reconcile_bootstrap_agent_profile` — synchronizes live upstream
  inventory and materializes the matching safe built-in default as an active
  bootstrap profile when no durable agent-profile state exists; and
* :func:`reconcile_managed_default_agent_profile` — the single boundary that
  decides which MoonMind-managed profile holds ``default_for_runtime``. The
  built-in OpenCode profile is the deployment default whenever it is launch
  ready (MoonLadderStudios/MoonMind#3877); this Codex bootstrap profile is the
  fallback, and explicit operator authority is never displaced.

This profile carries no credentials or host authority. Catalog readiness still
checks the live bridge, provider OAuth profile, immutable launch policy, worker,
and enforced network before it advertises the runtime as available.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    OmnigentAgentProfile,
    OmnigentAgentProfileAuditEvent,
    OmnigentAgentProfileVersion,
    OmnigentPolicy,
    OmnigentPolicyVersion,
    OmnigentUpstreamAgentProjection,
)
from api_service.services.omnigent_agent_profile_service import (
    projection_identity,
    projection_readiness,
    synchronize_upstream_inventory,
)

logger = logging.getLogger(__name__)

BOOTSTRAP_PROFILE_ID = "omnigent-bootstrap-default"
OPENCODE_BUILTIN_PROFILE_ID = "omnigent-opencode-default"
_ENV_DEFAULT_AGENT_NAME = "OMNIGENT_DEFAULT_AGENT_NAME"
_BUILTIN_DEFAULT_AGENT_NAME = "codex-native-ui"

# MoonLadderStudios/MoonMind#3877: the deployment default runtime is Omnigent
# (OpenCode), so the built-in OpenCode profile is the preferred managed default
# and the Codex bootstrap profile is the fallback. Ordering is the preference.
_MANAGED_DEFAULT_PREFERENCE = (OPENCODE_BUILTIN_PROFILE_ID, BOOTSTRAP_PROFILE_ID)

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


def _bootstrap_agent_name(env: Mapping[str, Any] | None) -> tuple[str, str]:
    env_name = _env_default_agent_name(env)
    if env_name:
        return env_name, "env_bootstrap"
    return _BUILTIN_DEFAULT_AGENT_NAME, "builtin_default"


def _digest(document: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_bootstrap_document(
    agent_name: str,
    *,
    upstream_version: str | None = None,
    launch_policy_ref: str = _BOOTSTRAP_LAUNCH_POLICY_REF,
) -> dict[str, Any]:
    """Build the normalized bootstrap profile document for a stable agent ID.

    The environment provides only a stable upstream agent selector; the
    remaining fields use the canonical Codex-via-Omnigent defaults so the
    materialized version is a coherent launch authority after upstream
    synchronization. The result is the fully-normalized document form (identical
    to what the profile API persists for an equivalent create) so digests
    stay consistent across the system without coupling to the router models.
    """

    return {
        "bridgeMode": _BOOTSTRAP_BRIDGE_MODE,
        "capture": {"evidence": True, "stream": True},
        "continuations": {"branch": True, "checkpoint": True, "remediation": True},
        "endpointRef": _BOOTSTRAP_ENDPOINT_REF,
        "execution": {
            "allowedLaunchPolicyRefs": [launch_policy_ref],
            "defaultExecutionProfileRef": _BOOTSTRAP_EXECUTION_PROFILE_REF,
        },
        "harness": _BOOTSTRAP_HARNESS,
        "model": {"settings": {}},
        "policyRef": launch_policy_ref,
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
        "source": {
            "upstreamId": agent_name,
            **({"upstreamVersion": upstream_version} if upstream_version else {}),
        },
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
    upstream_id: str | None = None,
    upstream_version: str | None = None,
    launch_policy_ref: str = _BOOTSTRAP_LAUNCH_POLICY_REF,
) -> str | None:
    """Materialize an active profile only for a synchronized upstream identity.

    Idempotent and fail-closed: skips when any durable agent-profile state
    already exists (durable state wins). Returns the seeded profile id, or
    ``None`` when nothing was materialized.
    """

    agent_name, origin = _bootstrap_agent_name(env)
    stable_upstream_id = _clean(upstream_id) or agent_name

    existing_count = int(
        await session.scalar(select(func.count()).select_from(OmnigentAgentProfile))
        or 0
    )
    if existing_count:
        # Durable state already exists; never overwrite it from the env value.
        return None

    observed_at = now or datetime.now(timezone.utc)
    projection = await session.get(
        OmnigentUpstreamAgentProjection,
        projection_identity(
            _BOOTSTRAP_ENDPOINT_REF,
            stable_upstream_id,
            upstream_version,
        ),
    )
    upstream_readiness = projection_readiness(
        projection,
        now=observed_at,
        bridge_mode=_BOOTSTRAP_BRIDGE_MODE,
        harness=_BOOTSTRAP_HARNESS,
        required_capabilities=_BOOTSTRAP_REQUIRED_CAPABILITIES,
    )
    if not upstream_readiness["ready"]:
        return None

    document = build_bootstrap_document(
        stable_upstream_id,
        upstream_version=upstream_version,
        launch_policy_ref=launch_policy_ref,
    )
    profile = OmnigentAgentProfile(
        profile_id=BOOTSTRAP_PROFILE_ID,
        display_name="Codex via Omnigent",
        description=(
            "MoonMind-managed portable default for Codex execution through "
            "Omnigent. Runtime launch authority remains policy- and "
            "readiness-gated."
        ),
        owner_id=None,
        visibility="workspace",
        state="active",
        active_version=1,
        default_for_runtime=True,
    )
    version = OmnigentAgentProfileVersion(
        profile_id=BOOTSTRAP_PROFILE_ID,
        version=1,
        digest=_digest(document),
        document=document,
        created_by=None,
        upstream_snapshot=projection.metadata_snapshot,
        validation_result={
            "schemaVersion": "moonmind.omnigent-agent-profile-validation.v1",
            "ready": True,
            "checks": [
                {
                    "id": "portable_builtin_contract",
                    "status": "ready",
                    "message": (
                        "The built-in Codex profile is structurally ready; "
                        "live launch dependencies are checked by the runtime catalog."
                    ),
                }
            ],
        },
        rollout_metadata={
            "origin": origin,
            **({"envVar": _ENV_DEFAULT_AGENT_NAME} if origin == "env_bootstrap" else {}),
            "materializedAt": observed_at.isoformat(),
        },
    )
    audit = OmnigentAgentProfileAuditEvent(
        profile_id=BOOTSTRAP_PROFILE_ID,
        action="bootstrap_materialized",
        version=1,
        actor_id=None,
        metadata_json={
            "origin": origin,
            **({"envVar": _ENV_DEFAULT_AGENT_NAME} if origin == "env_bootstrap" else {}),
            "state": "active",
            "defaultForRuntime": True,
        },
    )
    session.add_all([profile, version, audit])
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent startup already seeded the bootstrap profile.
        await session.rollback()
        return None
    logger.info(
        "Materialized active Omnigent bootstrap agent profile '%s' from %s",
        BOOTSTRAP_PROFILE_ID,
        origin,
    )
    return BOOTSTRAP_PROFILE_ID


def _is_v2_document(document: Mapping[str, Any]) -> bool:
    return document.get("schemaVersion") == "moonmind.omnigent-agent-profile.v2"


def _document_bridge_mode(document: Mapping[str, Any]) -> str | None:
    """Return the bridge-mode contract a document actually asserts.

    v2 documents bind their execution substrate through the harness catalog
    rather than a document-level bridge mode, so asserting one would compare a
    projection against a contract the document never declared.
    """

    if _is_v2_document(document):
        return None
    return str(document.get("bridgeMode") or "")


def _document_harness_id(document: Mapping[str, Any]) -> str:
    harness = document.get("harness")
    if _is_v2_document(document):
        return _clean((harness or {}).get("id") if isinstance(harness, Mapping) else "")
    return str(harness or "")


def _document_required_capabilities(document: Mapping[str, Any]) -> tuple[str, ...]:
    if _is_v2_document(document):
        requirements = document.get("requirements") or {}
        moonmind = requirements.get("moonmind") or {}
        return tuple(str(value) for value in (moonmind.get("required") or ()))
    return tuple(str(value) for value in (document.get("requiredCapabilities") or ()))


async def _agent_profile_launch_ready(
    session: AsyncSession,
    profile: OmnigentAgentProfile | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a profile and its observed identity are launch ready.

    One predicate for both the durable default and any managed candidate for
    that authority, so a default is never moved onto a profile whose upstream
    identity cannot satisfy the document contract.
    """

    if profile is None or profile.state != "active" or profile.active_version is None:
        return False
    version = await _load_active_version(session, profile)
    if (
        version is None
        or not version.validation_result
        or version.validation_result.get("ready") is not True
    ):
        return False
    document = version.document or {}
    source = document.get("source") or {}
    upstream_id = _clean(source.get("upstreamId"))
    if not upstream_id:
        return bool(source.get("bundleArtifactRef") and version.upstream_snapshot)
    projection = await session.get(
        OmnigentUpstreamAgentProjection,
        projection_identity(
            str(document.get("endpointRef") or ""),
            upstream_id,
            _clean(source.get("upstreamVersion")) or None,
        ),
    )
    return bool(
        projection_readiness(
            projection,
            now=now,
            bridge_mode=_document_bridge_mode(document),
            harness=_document_harness_id(document),
            required_capabilities=_document_required_capabilities(document),
        )["ready"]
    )


async def _operator_selected_default(session: AsyncSession, profile_id: str) -> bool:
    """Return whether this profile holds the current operator default selection.

    ``made_default`` audit rows are immutable evidence, so the row a profile
    earned stays behind after a later ``/default`` request selects a different
    profile. Only the most recent ``made_default`` event is a live operator
    claim; every earlier one is superseded. Treating a superseded row as current
    authority would permanently pin reconciliation to a profile the operator
    already replaced, even once that profile stops being launch ready.
    """

    latest_selection = await session.scalar(
        select(OmnigentAgentProfileAuditEvent.profile_id)
        .where(OmnigentAgentProfileAuditEvent.action == "made_default")
        .order_by(
            OmnigentAgentProfileAuditEvent.created_at.desc(),
            OmnigentAgentProfileAuditEvent.id.desc(),
        )
        .limit(1)
    )
    return latest_selection == profile_id


async def reconcile_managed_default_agent_profile(
    session: AsyncSession,
    *,
    env: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> str | None:
    """Hold the deployment default on the preferred MoonMind-managed profile.

    MoonLadderStudios/MoonMind#3877: the default workflow runtime is Omnigent
    (OpenCode) and its default Provider Profile is the credentialless OpenCode
    Zen seed, so a default launch must resolve through the built-in OpenCode
    Agent Profile whenever that profile is launch ready. The Codex bootstrap
    default keeps authority only while the OpenCode built-in cannot launch (for
    example when ``MOONMIND_OMNIGENT_OPENCODE_ENABLED=false``).

    Explicit authority always wins: an operator-authored default profile, an
    operator ``make_default`` selection, and an ``OMNIGENT_DEFAULT_AGENT_NAME``
    override are all preserved. Returns the profile id that holds the default.
    """

    current = await session.scalar(
        select(OmnigentAgentProfile).where(
            OmnigentAgentProfile.default_for_runtime.is_(True)
        )
    )
    current_id = current.profile_id if current is not None else None
    if current_id is not None and current_id not in _MANAGED_DEFAULT_PREFERENCE:
        # Operator-authored profiles are explicit launch authority.
        return current_id
    if _env_default_agent_name(env):
        # The documented bootstrap override selects the agent identity itself.
        return current_id
    if current_id is not None and await _operator_selected_default(session, current_id):
        return current_id

    preferred: OmnigentAgentProfile | None = None
    for profile_id in _MANAGED_DEFAULT_PREFERENCE:
        candidate = await session.get(OmnigentAgentProfile, profile_id)
        if candidate is None or not await _agent_profile_launch_ready(
            session, candidate, now=now
        ):
            continue
        preferred = candidate
        break
    if preferred is None:
        if current is None:
            return None
        # Every explicit authority (operator-authored profile, current operator
        # selection, OMNIGENT_DEFAULT_AGENT_NAME) already returned above, so the
        # remaining default is deployment-managed and no managed profile can
        # launch. Leaving the flag set would publish stale launch authority:
        # resolve_default_agent_profile_snapshot only requires an active profile
        # with a version, not a ready validation/projection, so a default
        # submission would resolve an unlaunchable profile instead of reporting
        # that no default is available.
        cleared_at = now or datetime.now(timezone.utc)
        current.default_for_runtime = False
        session.add(
            OmnigentAgentProfileAuditEvent(
                profile_id=current_id,
                action="managed_default_cleared",
                version=current.active_version,
                actor_id=None,
                metadata_json={
                    "previousProfileId": current_id,
                    "origin": "deployment_managed_default",
                    "clearedAt": cleared_at.isoformat(),
                    "reason": "no_launch_ready_managed_profile",
                },
            )
        )
        await session.commit()
        logger.info(
            "Cleared the deployment-managed default Omnigent agent profile %s "
            "because no managed profile is launch ready",
            current_id,
        )
        return None
    if preferred.profile_id == current_id:
        return current_id

    observed_at = now or datetime.now(timezone.utc)
    await session.execute(
        update(OmnigentAgentProfile)
        .where(OmnigentAgentProfile.default_for_runtime.is_(True))
        .values(default_for_runtime=False)
    )
    preferred.default_for_runtime = True
    session.add(
        OmnigentAgentProfileAuditEvent(
            profile_id=preferred.profile_id,
            action="managed_default_selected",
            version=preferred.active_version,
            actor_id=None,
            metadata_json={
                "previousProfileId": current_id,
                "origin": "deployment_managed_default",
                "selectedAt": observed_at.isoformat(),
            },
        )
    )
    await session.commit()
    logger.info(
        "Moved the deployment-managed default Omnigent agent profile from %s to %s",
        current_id,
        preferred.profile_id,
    )
    return preferred.profile_id


def _inventory_text(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return ""


async def default_agent_profile_ready(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether the durable default and its observed identity are ready."""

    profile = await session.scalar(
        select(OmnigentAgentProfile).where(
            OmnigentAgentProfile.default_for_runtime.is_(True)
        )
    )
    return await _agent_profile_launch_ready(session, profile, now=now)


async def _active_bootstrap_launch_policy_ref(session: AsyncSession) -> str:
    """Resolve the active built-in policy without embedding its version."""

    policy = await session.get(OmnigentPolicy, "codex-on-demand")
    if policy is None or policy.default_version is None:
        return _BOOTSTRAP_LAUNCH_POLICY_REF
    version = await session.scalar(
        select(OmnigentPolicyVersion).where(
            OmnigentPolicyVersion.policy_id == policy.policy_id,
            OmnigentPolicyVersion.version == policy.default_version,
        )
    )
    if (
        version is None
        or version.state != "active"
        or not version.validation_json
        or version.validation_json.get("valid") is not True
    ):
        return _BOOTSTRAP_LAUNCH_POLICY_REF
    return f"{policy.policy_id}@{version.version}"


async def _reconcile_bootstrap_profile_launch_policy(
    session: AsyncSession,
    *,
    launch_policy_ref: str,
    now: datetime,
) -> bool:
    """Advance the managed profile when its immutable policy authority moves."""

    profile = await session.get(OmnigentAgentProfile, BOOTSTRAP_PROFILE_ID)
    if (
        profile is None
        or profile.state != "active"
        or profile.active_version is None
    ):
        return False
    active = await _load_active_version(session, profile)
    if active is None or not isinstance(active.document, Mapping):
        return False
    rollout = active.rollout_metadata or {}
    if rollout.get("origin") not in {
        "builtin_default",
        "env_bootstrap",
        "bootstrap_policy_cutover",
    }:
        return False
    execution = active.document.get("execution")
    if not isinstance(execution, Mapping):
        return False
    if (
        execution.get("allowedLaunchPolicyRefs") == [launch_policy_ref]
        and active.document.get("policyRef") == launch_policy_ref
    ):
        return True

    document = copy.deepcopy(active.document)
    document["execution"]["allowedLaunchPolicyRefs"] = [launch_policy_ref]
    document["policyRef"] = launch_policy_ref
    digest = _digest(document)
    candidate = await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == BOOTSTRAP_PROFILE_ID,
            OmnigentAgentProfileVersion.digest == digest,
        )
    )
    if candidate is None:
        latest = int(
            await session.scalar(
                select(func.max(OmnigentAgentProfileVersion.version)).where(
                    OmnigentAgentProfileVersion.profile_id == BOOTSTRAP_PROFILE_ID
                )
            )
            or 0
        )
        candidate = OmnigentAgentProfileVersion(
            profile_id=BOOTSTRAP_PROFILE_ID,
            version=latest + 1,
            digest=digest,
            document=document,
            parent_version=active.version,
            upstream_snapshot=copy.deepcopy(active.upstream_snapshot),
            validation_result=copy.deepcopy(active.validation_result),
            rollout_metadata={
                "origin": "bootstrap_policy_cutover",
                "previousOrigin": rollout.get("origin"),
                "previousLaunchPolicyRef": active.document.get("policyRef"),
                "launchPolicyRef": launch_policy_ref,
                "materializedAt": now.isoformat(),
            },
            created_by=None,
        )
        session.add(candidate)
    profile.active_version = candidate.version
    session.add(
        OmnigentAgentProfileAuditEvent(
            profile_id=BOOTSTRAP_PROFILE_ID,
            action="bootstrap_launch_policy_cutover",
            version=candidate.version,
            actor_id=None,
            metadata_json={
                "previousVersion": active.version,
                "launchPolicyRef": launch_policy_ref,
                "state": "active",
            },
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # A concurrent startup may have completed the same immutable cutover.
        profile = await session.get(OmnigentAgentProfile, BOOTSTRAP_PROFILE_ID)
        if profile is None:
            return False
        current = await _load_active_version(session, profile)
        return bool(
            current is not None
            and current.digest == digest
            and current.document.get("policyRef") == launch_policy_ref
        )
    return True


async def reconcile_bootstrap_agent_profile(
    session: AsyncSession,
    *,
    inventory: list[Mapping[str, Any]],
    env: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Synchronize observed inventory, then materialize the built-in default."""

    observed_at = now or datetime.now(timezone.utc)
    await synchronize_upstream_inventory(
        session,
        endpoint_ref=_BOOTSTRAP_ENDPOINT_REF,
        bridge_mode=_BOOTSTRAP_BRIDGE_MODE,
        inventory=inventory,
        now=observed_at,
    )
    selector, _ = _bootstrap_agent_name(env)
    exact = next(
        (
            item
            for item in inventory
            if _inventory_text(item, "id", "agentId", "agent_id") == selector
        ),
        None,
    )
    candidate = exact or next(
        (
            item
            for item in inventory
            if _inventory_text(item, "name", "displayName") == selector
        ),
        None,
    )
    if candidate is None:
        return False
    upstream_id = _inventory_text(candidate, "id", "agentId", "agent_id")
    if not upstream_id:
        return False
    upstream_version = (
        _inventory_text(candidate, "version", "agentVersion", "agent_version")
        or None
    )
    launch_policy_ref = await _active_bootstrap_launch_policy_ref(session)
    await seed_bootstrap_agent_profile(
        session,
        env=env,
        now=observed_at,
        upstream_id=upstream_id,
        upstream_version=upstream_version,
        launch_policy_ref=launch_policy_ref,
    )
    await _reconcile_bootstrap_profile_launch_policy(
        session,
        launch_policy_ref=launch_policy_ref,
        now=observed_at,
    )
    # The Codex bootstrap profile is only the fallback managed default. Settle
    # managed default authority here as well as after catalog synchronization so
    # an OpenCode built-in that is already launch ready is never displaced by a
    # later bootstrap pass.
    await reconcile_managed_default_agent_profile(
        session, env=env, now=observed_at
    )
    return await default_agent_profile_ready(session, now=observed_at)


__all__ = [
    "BOOTSTRAP_PROFILE_ID",
    "OPENCODE_BUILTIN_PROFILE_ID",
    "BootstrapDefaultConflictError",
    "DefaultAgentResolution",
    "build_bootstrap_document",
    "default_agent_profile_ready",
    "reconcile_bootstrap_agent_profile",
    "reconcile_managed_default_agent_profile",
    "resolve_default_agent_selection",
    "seed_bootstrap_agent_profile",
]
