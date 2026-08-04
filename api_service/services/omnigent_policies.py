"""Persistent immutable Omnigent policy lifecycle service."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
import os
import platform
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    OmnigentBridgeSession,
    OmnigentOAuthHostBindingRecord,
    OmnigentPolicy,
    OmnigentPolicyEvent,
    OmnigentPolicyVersion,
)
from moonmind.config.container_backend_settings import (
    ContainerBackendConfigError,
    resolve_container_backend_settings,
)
from moonmind.omnigent.policies import (
    PolicyDocument,
    PolicyState,
    compile_policy_snapshot,
    document_digest,
    normalize_document,
)
from moonmind.security.egress import OMNIGENT_EGRESS_PROFILE
from moonmind.workflows.temporal.container_image_acquisition import (
    normalize_image_reference,
)
from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command


logger = logging.getLogger(__name__)

_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_SERVER_IMAGE_REPOSITORY = "ghcr.io/omnigent-ai/omnigent-server"
_HOST_IMAGE_REPOSITORY = "ghcr.io/omnigent-ai/omnigent-host"
_IMAGE_INSPECT_FORMAT = '{{.Id}}\t{{join .RepoDigests ","}}'
ImageResolver = Callable[[str], Awaitable[str | None]]
_BOOTSTRAP_POLICY_DEFINITIONS = (
    (
        "omnigent-codex",
        "Omnigent Codex execution",
        "static_compose",
        "omnigent-codex@1",
    ),
    (
        "codex-static",
        "Codex static host",
        "static_compose",
        "omnigent-codex@1",
    ),
    (
        "codex-on-demand",
        "Codex on-demand host",
        "on_demand_docker",
        "omnigent-codex@1",
    ),
)


class PolicyConflict(ValueError):
    pass


class PolicyNotFound(LookupError):
    pass


_BRIDGE_TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "timed_out"})
# Policy usage inspection is invoked by the UI on every version selection. Bound
# the dependent identifier lists it returns so response cost tracks the selected
# policy's useful page size rather than the deployment's entire bridge history;
# the accompanying counts still report true totals from the database.
_USAGE_DEPENDENT_PAGE_SIZE = 50


def _split_policy_ref(policy_ref: str) -> tuple[str, int]:
    policy_id, separator, version_text = policy_ref.rpartition("@")
    if not separator or not policy_id:
        raise PolicyConflict(f"invalid policy version reference: {policy_ref!r}")
    try:
        version = int(version_text)
    except ValueError as exc:
        raise PolicyConflict(f"invalid policy version reference: {policy_ref!r}") from exc
    if version < 1:
        raise PolicyConflict(f"invalid policy version reference: {policy_ref!r}")
    return policy_id, version


def validate_policy(
    document: PolicyDocument,
    *,
    capabilities: dict[str, set[str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return stable diagnostics consumed before credentials or host mutation."""

    diagnostics: list[dict[str, str]] = []
    payload = document.model_dump(by_alias=True, mode="json")
    image_pattern = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
    for field in ("serverImageRef", "hostImageRef"):
        image_ref = payload["host"][field]
        if (
            not image_pattern.fullmatch(image_ref)
            or image_ref.endswith("@sha256:" + "0" * 64)
        ):
            diagnostics.append({
                "code": "OMNIGENT_INVALID_IMAGE_REF",
                "path": f"host.{field}",
                "message": "Runtime images must use immutable sha256 digest references.",
            })
    if not payload["capture"]["required"]:
        diagnostics.append({
            "code": "OMNIGENT_CAPTURE_AUTHORITY_MISSING",
            "path": "capture.required",
            "message": "Activation requires complete capture authority.",
        })
    if payload["network"]["egressProfileRef"] == payload["network"]["attachmentRef"]:
        diagnostics.append({
            "code": "OMNIGENT_ENFORCED_EGRESS_MISSING",
            "path": "network.egressProfileRef",
            "message": "Network attachment and enforced-egress authority must be distinct references.",
        })
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    try:
        container_backend_enabled = resolve_container_backend_settings().enabled
    except ContainerBackendConfigError:
        container_backend_enabled = False
    deployment_capabilities = {
        "hostModes": {"static_compose"} | ({"on_demand_docker"} if container_backend_enabled else set()),
        "backends": {"compose"} | ({"container-backend"} if container_backend_enabled else set()),
        "architectures": {architecture},
        "providers": {"codex"},
        "workspaceClasses": {"workflow"},
    }
    declared = capabilities or deployment_capabilities
    capability_checks = (
        ("hostModes", payload["host"]["mode"], "host.mode", "OMNIGENT_HOST_MODE_UNAVAILABLE"),
        ("backends", payload["host"]["backendRef"], "host.backendRef", "OMNIGENT_BACKEND_UNAVAILABLE"),
    )
    for capability, value, path, code in capability_checks:
        if value not in declared.get(capability, set()):
            diagnostics.append({"code": code, "path": path, "message": f"{value!r} is not available in this deployment."})
    for capability, values, path, code in (
        ("architectures", payload["host"]["architectures"], "host.architectures", "OMNIGENT_ARCHITECTURE_UNAVAILABLE"),
        ("providers", payload["providerProfile"]["compatibleProviders"], "providerProfile.compatibleProviders", "OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE"),
        ("workspaceClasses", payload["workspace"]["allowedClasses"], "workspace.allowedClasses", "OMNIGENT_WORKSPACE_CLASS_UNSUPPORTED"),
    ):
        unsupported = sorted(set(values) - declared.get(capability, set()))
        if unsupported:
            diagnostics.append({"code": code, "path": path, "message": f"Unsupported values: {', '.join(unsupported)}."})
    valid = not diagnostics
    return (
        {"valid": valid, "diagnostics": diagnostics, "validatedAt": datetime.now(UTC).isoformat()},
        {"compatible": valid, "diagnosticCodes": [item["code"] for item in diagnostics]},
    )


class OmnigentPolicyService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self) -> list[tuple[OmnigentPolicy, OmnigentPolicyVersion | None]]:
        policies = (await self.session.execute(select(OmnigentPolicy).order_by(OmnigentPolicy.name))).scalars().all()
        result = []
        for policy in policies:
            version = None
            if policy.default_version is not None:
                version = await self.get_version(policy.policy_id, policy.default_version)
            else:
                version = (await self.session.execute(
                    select(OmnigentPolicyVersion)
                    .where(OmnigentPolicyVersion.policy_id == policy.policy_id)
                    .order_by(OmnigentPolicyVersion.version.desc())
                    .limit(1)
                )).scalar_one_or_none()
            result.append((policy, version))
        return result

    async def get_policy(self, policy_id: str) -> OmnigentPolicy:
        policy = await self.session.get(OmnigentPolicy, policy_id)
        if policy is None:
            raise PolicyNotFound(policy_id)
        return policy

    async def get_version(self, policy_id: str, version: int) -> OmnigentPolicyVersion:
        row = (await self.session.execute(select(OmnigentPolicyVersion).where(
            OmnigentPolicyVersion.policy_id == policy_id,
            OmnigentPolicyVersion.version == version,
        ))).scalar_one_or_none()
        if row is None:
            raise PolicyNotFound(f"{policy_id}@{version}")
        return row

    async def versions(self, policy_id: str) -> list[OmnigentPolicyVersion]:
        await self.get_policy(policy_id)
        return list((await self.session.execute(select(OmnigentPolicyVersion).where(
            OmnigentPolicyVersion.policy_id == policy_id
        ).order_by(OmnigentPolicyVersion.version.desc()))).scalars())

    async def create(self, *, policy_id: str, name: str, owner_user_id: Any, visibility: str,
                     document: PolicyDocument, actor: str, clone_source_ref: str | None = None) -> OmnigentPolicyVersion:
        if await self.session.get(OmnigentPolicy, policy_id):
            raise PolicyConflict("policy identity already exists")
        if clone_source_ref is not None:
            source_policy_id, source_version = _split_policy_ref(clone_source_ref)
            try:
                await self.get_version(source_policy_id, source_version)
            except PolicyNotFound as exc:
                raise PolicyConflict(
                    f"clone source does not exist: {clone_source_ref}"
                ) from exc
        policy = OmnigentPolicy(policy_id=policy_id, name=name, owner_user_id=owner_user_id, visibility=visibility)
        self.session.add(policy)
        row = self._version(policy_id, 1, document, actor, clone_source_ref=clone_source_ref)
        self.session.add(row)
        self._event(policy_id, 1, "version_created", actor, {
            "cloneSourceRef": clone_source_ref, "digest": row.digest,
        })
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise PolicyConflict("policy identity or name already exists") from exc
        return row

    async def new_version(self, *, policy_id: str, document: PolicyDocument, actor: str,
                          expected_parent_ref: str) -> OmnigentPolicyVersion:
        # Serialize allocation on the stable identity. The unique constraint is
        # the final authority on engines where row locking is unavailable.
        policy = (await self.session.execute(
            select(OmnigentPolicy)
            .where(OmnigentPolicy.policy_id == policy_id)
            .with_for_update()
        )).scalar_one_or_none()
        if policy is None:
            raise PolicyNotFound(policy_id)
        latest = (await self.session.execute(select(func.max(OmnigentPolicyVersion.version)).where(
            OmnigentPolicyVersion.policy_id == policy_id
        ))).scalar_one()
        if expected_parent_ref != f"{policy_id}@{latest}":
            raise PolicyConflict("stale policy version; reload before editing")
        row = self._version(policy_id, latest + 1, document, actor, parent_ref=expected_parent_ref)
        self.session.add(row)
        self._event(policy_id, row.version, "version_created", actor, {
            "parentRef": expected_parent_ref, "digest": row.digest,
        })
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise PolicyConflict("concurrent policy edit; reload before editing") from exc
        return row

    async def transition(self, *, policy_id: str, version: int, state: PolicyState, actor: str,
                         make_default: bool = False) -> OmnigentPolicyVersion:
        policy = (await self.session.execute(
            select(OmnigentPolicy)
            .where(OmnigentPolicy.policy_id == policy_id)
            .with_for_update()
        )).scalar_one_or_none()
        if policy is None:
            raise PolicyNotFound(policy_id)
        row = (await self.session.execute(
            select(OmnigentPolicyVersion)
            .where(
                OmnigentPolicyVersion.policy_id == policy_id,
                OmnigentPolicyVersion.version == version,
            )
            .with_for_update()
        )).scalar_one_or_none()
        if row is None:
            raise PolicyNotFound(f"{policy_id}@{version}")
        allowed = {
            PolicyState.DRAFT: {PolicyState.ACTIVE, PolicyState.DISABLED},
            PolicyState.ACTIVE: {PolicyState.DISABLED, PolicyState.DEPRECATED, PolicyState.SUPERSEDED},
            PolicyState.DEPRECATED: {PolicyState.ACTIVE, PolicyState.DISABLED},
            PolicyState.DISABLED: {PolicyState.ACTIVE},
            PolicyState.SUPERSEDED: {PolicyState.ACTIVE},
        }
        current = PolicyState(row.state)
        if state != current and state not in allowed[current]:
            raise PolicyConflict(f"invalid policy transition: {current.value} -> {state.value}")
        if state == PolicyState.ACTIVE and not row.validation_json.get("valid"):
            raise PolicyConflict("invalid policy cannot be activated")
        if (
            state in {PolicyState.DISABLED, PolicyState.DEPRECATED, PolicyState.SUPERSEDED}
            and policy.default_version == version
        ):
            raise PolicyConflict(
                "default policy version cannot be made unavailable; switch the default first"
            )
        if state in {PolicyState.DISABLED, PolicyState.DEPRECATED, PolicyState.SUPERSEDED}:
            bound = (await self.session.execute(
                select(OmnigentOAuthHostBindingRecord.binding_ref).where(
                    OmnigentOAuthHostBindingRecord.launch_policy_ref
                    == f"{policy_id}@{version}"
                ).limit(1)
            )).scalar_one_or_none()
            if bound is not None:
                raise PolicyConflict(
                    "policy version is bound to an active host profile and cannot be made unavailable"
                )
            policy_ref = f"{policy_id}@{version}"
            # Bound this safety gate to the selected policy in the database
            # instead of scanning the deployment's entire bridge history.
            active_bridge = (await self.session.execute(
                select(OmnigentBridgeSession.bridge_session_id)
                .where(
                    OmnigentBridgeSession.effective_launch_snapshot_json[
                        "policyAuthority"
                    ]["policyRef"].as_string()
                    == policy_ref,
                    func.lower(OmnigentBridgeSession.status).notin_(
                        _BRIDGE_TERMINAL_STATES
                    ),
                )
                .limit(1)
            )).scalar_one_or_none()
            if active_bridge is not None:
                raise PolicyConflict(
                    "policy version is bound to an active bridge session and cannot be made unavailable"
                )
        now = datetime.now(UTC)
        row.state = state.value
        if state == PolicyState.ACTIVE:
            row.activated_by, row.activated_at = actor, now
            if policy.default_version not in (None, version) and not row.supersedes_ref:
                row.supersedes_ref = f"{policy_id}@{policy.default_version}"
        if state == PolicyState.DISABLED:
            row.disabled_by, row.disabled_at = actor, now
        if make_default:
            if state != PolicyState.ACTIVE:
                raise PolicyConflict("only an active version can be the default")
            previous_default = policy.default_version
            policy.default_version = version
            self._event(policy_id, version, "default_changed", actor, {
                "previousVersion": previous_default,
                "newVersion": version,
            })
        self._event(policy_id, version, "lifecycle_transition", actor, {
            "from": current.value, "to": state.value, "makeDefault": make_default,
        })
        await self.session.commit()
        return row

    async def audit(self, policy_id: str) -> list[OmnigentPolicyEvent]:
        await self.get_policy(policy_id)
        return list((await self.session.execute(
            select(OmnigentPolicyEvent)
            .where(OmnigentPolicyEvent.policy_id == policy_id)
            .order_by(OmnigentPolicyEvent.created_at, OmnigentPolicyEvent.event_id)
        )).scalars())

    async def usage(self, policy_id: str, version: int) -> dict[str, Any]:
        """Return persisted dependents and lifecycle impact for one immutable version."""

        policy = await self.get_policy(policy_id)
        row = await self.get_version(policy_id, version)
        policy_ref = f"{policy_id}@{version}"
        host_bindings = list((await self.session.execute(
            select(OmnigentOAuthHostBindingRecord.binding_ref).where(
                OmnigentOAuthHostBindingRecord.launch_policy_ref == policy_ref
            ).order_by(OmnigentOAuthHostBindingRecord.binding_ref)
        )).scalars())
        # Bridge sessions keep the complete immutable authority in their launch
        # snapshot. Filter the persisted policy reference in the database so
        # inspection cost tracks the selected policy's dependents, not the
        # deployment's entire bridge history. Return true counts plus a bounded
        # page of dependent identifiers. Resolving from persisted evidence keeps
        # historical usage inspectable after rollout.
        matches_policy = (
            OmnigentBridgeSession.effective_launch_snapshot_json["policyAuthority"][
                "policyRef"
            ].as_string()
            == policy_ref
        )
        active_predicate = func.lower(OmnigentBridgeSession.status).notin_(
            _BRIDGE_TERMINAL_STATES
        )
        bridge_session_count = (await self.session.execute(
            select(func.count()).select_from(OmnigentBridgeSession).where(matches_policy)
        )).scalar_one()
        active_bridge_session_count = (await self.session.execute(
            select(func.count())
            .select_from(OmnigentBridgeSession)
            .where(matches_policy, active_predicate)
        )).scalar_one()
        workflow_count = (await self.session.execute(
            select(func.count(func.distinct(OmnigentBridgeSession.moonmind_workflow_id)))
            .where(matches_policy)
        )).scalar_one()
        bridge_sessions = list((await self.session.execute(
            select(OmnigentBridgeSession.bridge_session_id)
            .where(matches_policy)
            .order_by(OmnigentBridgeSession.bridge_session_id)
            .limit(_USAGE_DEPENDENT_PAGE_SIZE)
        )).scalars())
        active_bridge_sessions = list((await self.session.execute(
            select(OmnigentBridgeSession.bridge_session_id)
            .where(matches_policy, active_predicate)
            .order_by(OmnigentBridgeSession.bridge_session_id)
            .limit(_USAGE_DEPENDENT_PAGE_SIZE)
        )).scalars())
        workflow_ids = list((await self.session.execute(
            select(OmnigentBridgeSession.moonmind_workflow_id)
            .where(matches_policy)
            .distinct()
            .order_by(OmnigentBridgeSession.moonmind_workflow_id)
            .limit(_USAGE_DEPENDENT_PAGE_SIZE)
        )).scalars())
        provider_profile_ids = {
            str(value)
            for value in (await self.session.execute(
                select(OmnigentOAuthHostBindingRecord.provider_profile_id).where(
                    OmnigentOAuthHostBindingRecord.launch_policy_ref == policy_ref
                )
            )).scalars()
            if value
        }
        provider_profile_ids.update(
            str(value)
            for value in (await self.session.execute(
                select(OmnigentBridgeSession.provider_profile_id)
                .where(matches_policy, OmnigentBridgeSession.provider_profile_id.is_not(None))
                .distinct()
            )).scalars()
            if value
        )

        is_default = policy.default_version == version
        blockers = []
        if is_default:
            blockers.append("Switch the policy default before disabling or deprecating this version.")
        if host_bindings:
            blockers.append("Move dependent host profiles before disabling or deprecating this version.")
        if active_bridge_session_count:
            blockers.append("Wait for dependent bridge sessions to finish before disabling or deprecating this version.")
        return {
            "policyRef": policy_ref,
            "state": row.state,
            "default": is_default,
            "dependents": {
                "hostBindings": host_bindings,
                "hostBindingCount": len(host_bindings),
                "providerProfiles": sorted(provider_profile_ids),
                "providerProfileCount": len(provider_profile_ids),
                "workflows": workflow_ids,
                "workflowCount": workflow_count,
                "bridgeSessions": bridge_sessions,
                "bridgeSessionCount": bridge_session_count,
                "activeBridgeSessions": active_bridge_sessions,
                "activeBridgeSessionCount": active_bridge_session_count,
            },
            "activationImpact": {
                "willSwitchDefault": not is_default,
                "compatible": bool(row.compatibility_json.get("compatible")),
                "diagnostics": row.validation_json.get("diagnostics", []),
            },
            "unavailabilityBlockers": blockers,
        }

    async def snapshot(self, policy_id: str, version: int) -> dict[str, Any]:
        row = await self.get_version(policy_id, version)
        return compile_policy_snapshot(policy_id=policy_id, version=version, document=row.document_json, validation=row.validation_json)

    async def resolve_runtime_snapshot(self, policy_ref: str) -> dict[str, Any]:
        """Resolve exact, active runtime authority before any external side effect."""

        policy_id, version = _split_policy_ref(policy_ref)
        row = await self.get_version(policy_id, version)
        if row.state != PolicyState.ACTIVE.value:
            raise PolicyConflict(f"runtime policy is not active: {policy_ref}")
        if not row.validation_json.get("valid"):
            raise PolicyConflict(f"runtime policy is not valid: {policy_ref}")
        snapshot = compile_policy_snapshot(
            policy_id=policy_id,
            version=version,
            document=row.document_json,
            validation=row.validation_json,
        )
        if snapshot["policyDigest"] != row.digest:
            raise PolicyConflict(f"runtime policy digest conflict: {policy_ref}")
        return snapshot

    async def resolve_default_runtime_snapshot(
        self, policy_id: str
    ) -> dict[str, Any]:
        """Resolve a policy's durable default as exact runtime authority.

        Environment-backed values are bootstrap inputs only.  Normal runtime
        callers must resolve the persisted default and then apply the same
        active/validation/digest gates as an explicitly selected version.
        """

        policy = await self.get_policy(policy_id)
        if policy.default_version is None:
            raise PolicyConflict(
                f"runtime policy has no default version: {policy_id}"
            )
        return await self.resolve_runtime_snapshot(
            f"{policy.policy_id}@{policy.default_version}"
        )

    @staticmethod
    def _version(policy_id: str, version: int, document: PolicyDocument, actor: str, **lineage: Any) -> OmnigentPolicyVersion:
        normalized = normalize_document(document)
        validation, compatibility = validate_policy(document)
        return OmnigentPolicyVersion(
            id=uuid4(), policy_id=policy_id, version=version, state="draft",
            document_json=normalized, digest=document_digest(normalized),
            created_by=actor, validation_json=validation,
            compatibility_json=compatibility, rollout_json=normalized["rollout"], **lineage,
        )

    def _event(
        self, policy_id: str, version: int | None, event_type: str,
        actor: str, detail: dict[str, Any],
    ) -> None:
        self.session.add(OmnigentPolicyEvent(
            policy_id=policy_id, version=version, event_type=event_type,
            actor=actor, detail_json=detail,
        ))


def _configured_image_ref(
    *,
    env: Mapping[str, str],
    ref_variable: str,
    repository_variable: str,
    tag_variable: str,
    default_repository: str,
) -> str:
    explicit = str(env.get(ref_variable, "") or "").strip()
    if explicit:
        return explicit
    repository = str(env.get(repository_variable, "") or "").strip()
    repository = repository or default_repository
    if "@" in repository:
        return repository
    tag = str(env.get(tag_variable, "") or "").strip() or "latest"
    return f"{repository}:{tag}"


def configured_bootstrap_image_refs(
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Return the operator-facing server and host image inputs.

    Mutable tags are intentionally accepted at this bootstrap boundary. They
    are resolved to repository digests before an immutable policy is created;
    mutable values never become launch authority.
    """

    source = os.environ if env is None else env
    return (
        _configured_image_ref(
            env=source,
            ref_variable="OMNIGENT_IMAGE_REF",
            repository_variable="OMNIGENT_IMAGE",
            tag_variable="OMNIGENT_IMAGE_TAG",
            default_repository=_SERVER_IMAGE_REPOSITORY,
        ),
        _configured_image_ref(
            env=source,
            ref_variable="OMNIGENT_HOST_IMAGE_REF",
            repository_variable="OMNIGENT_HOST_IMAGE",
            tag_variable="OMNIGENT_HOST_IMAGE_TAG",
            default_repository=_HOST_IMAGE_REPOSITORY,
        ),
    )


def _repository_digest(image_ref: str, inspect_output: bytes) -> str | None:
    """Select the exact repository digest that matches ``image_ref``."""

    _, _, raw_repo_digests = inspect_output.decode(
        "utf-8", errors="replace"
    ).strip().partition("\t")
    requested = normalize_image_reference(image_ref)
    for candidate in re.split(r"[,\s]+", raw_repo_digests):
        candidate = candidate.strip()
        if not _DIGEST_IMAGE.fullmatch(candidate):
            continue
        normalized = normalize_image_reference(candidate)
        if (
            normalized.registry == requested.registry
            and normalized.repository == requested.repository
        ):
            return candidate
    return None


async def resolve_bootstrap_image_ref(image_ref: str) -> str | None:
    """Acquire a stock image and return immutable launch authority.

    The API already has narrowly proxied Docker image authority for its
    existing runtime duties. Bootstrap uses that boundary to turn convenient
    tags (including ``latest``) into repository digests. If the registry is
    temporarily unavailable, a previously acquired local repository digest is
    still safe to use.
    """

    image_ref = image_ref.strip()
    if _DIGEST_IMAGE.fullmatch(image_ref):
        return image_ref

    docker_binary = os.getenv("MOONMIND_DOCKER_BINARY", "docker").strip() or "docker"

    async def inspect() -> str | None:
        code, stdout, _ = await run_runtime_command(
            (docker_binary, "image", "inspect", "--format", _IMAGE_INSPECT_FORMAT, image_ref),
            timeout_seconds=30,
            output_limit_bytes=64_000,
        )
        return None if code else _repository_digest(image_ref, stdout)

    try:
        local_digest = await inspect()
    except OSError:
        local_digest = None
    try:
        code, _, stderr = await run_runtime_command(
            (docker_binary, "pull", image_ref),
            timeout_seconds=600,
            output_limit_bytes=64_000,
        )
    except OSError:
        code, stderr = 1, b"Docker image acquisition was unavailable"
    if code:
        if local_digest:
            logger.warning(
                "Could not refresh Omnigent bootstrap image %s; using the "
                "previously acquired immutable repository digest",
                image_ref,
            )
            return local_digest
        logger.warning(
            "Could not acquire Omnigent bootstrap image %s: %s",
            image_ref,
            stderr.decode("utf-8", errors="replace").strip()
            or "Docker pull failed",
        )
        return None
    try:
        return await inspect() or local_digest
    except OSError:
        return local_digest


async def resolve_bootstrap_image_refs(
    *,
    env: Mapping[str, str] | None = None,
    image_resolver: ImageResolver = resolve_bootstrap_image_ref,
) -> tuple[str | None, str | None]:
    server_input, host_input = configured_bootstrap_image_refs(env)
    server_image = await image_resolver(server_input)
    host_image = await image_resolver(host_input)
    return server_image, host_image


# Every production remediation adapter registered by
# ``moonmind.workflows.temporal.remediation_actions.build_remediation_action_executor``
# dispatches by its canonical action identity, and ``resolve_action`` performs an
# exact-match lookup against ``approvals.actions``. The bootstrap policy must
# therefore carry an explicit rule for each identity or the default deployment
# authorizes none of them. Non-mutating evidence actions are allowed; every
# state-mutating remediation action stays approval-gated, matching this
# bootstrap's ``remediation.autonomous: False`` posture. The identities are
# listed explicitly (not imported) to keep the seed reviewable and to avoid a
# circular import with the temporal remediation package; a unit test guards
# against catalog drift.
_BOOTSTRAP_ALLOWED_REMEDIATION_ACTIONS: tuple[str, ...] = (
    "cleanup.verify",
    "target.annotate",
    "target.verify",
)
_BOOTSTRAP_APPROVAL_REMEDIATION_ACTIONS: tuple[str, ...] = (
    "execution.pause",
    "execution.resume",
    "execution.request_rerun_same_workflow",
    "execution.start_fresh_rerun",
    "execution.cancel",
    "execution.force_terminate",
    "checkpoint_branch.create_from_remediation_context",
    "session.interrupt_turn",
    "session.clear",
    "session.cancel",
    "session.terminate",
    "session.restart_container",
    "provider_profile.evict_stale_lease",
    "workload.restart_helper_container",
    "workload.reap_orphan_container",
    "host.drain",
    "host.stop",
    "host.restart",
    "host.remove",
    "host_lease.reconcile_stale",
    "cleanup.request_janitor",
)


def _bootstrap_approval_actions() -> dict[str, dict[str, str]]:
    """Seed exact approval rules for every canonical remediation action identity."""

    actions: dict[str, dict[str, str]] = {
        "read": {"decision": "allow"},
        "publish": {
            "decision": "approval_required",
            "approvalClass": "publication",
            "reviewerRule": "workflow-owner",
        },
    }
    for action in _BOOTSTRAP_ALLOWED_REMEDIATION_ACTIONS:
        actions[action] = {
            "decision": "allow",
            "reason": "non-mutating remediation evidence",
        }
    for action in _BOOTSTRAP_APPROVAL_REMEDIATION_ACTIONS:
        actions[action] = {
            "decision": "approval_required",
            "approvalClass": "remediation",
            "reviewerRule": "workflow-owner",
            "reason": "state-mutating remediation requires approval",
        }
    return actions


def bootstrap_document(
    *,
    host_mode: str,
    execution_profile_ref: str,
    server_image_ref: str | None = None,
    host_image_ref: str | None = None,
) -> PolicyDocument:
    """Project legacy built-ins into an explicit, reviewable bootstrap policy."""

    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    return PolicyDocument.model_validate({
        "schemaVersion": 1,
        "endpoint": {"ref": "default", "bridgeModes": ["embedded", "proxy"]},
        "execution": {"profileRef": execution_profile_ref, "harness": "codex-native", "agentIdentities": ["codex"]},
        "host": {"mode": host_mode, "backendRef": "compose" if host_mode == "static_compose" else "container-backend",
                 "architectures": [architecture],
                 "serverImageRef": server_image_ref if _DIGEST_IMAGE.fullmatch(server_image_ref or "") else "image-ref:omnigent-server",
                 "hostImageRef": host_image_ref if _DIGEST_IMAGE.fullmatch(host_image_ref or "") else "image-ref:omnigent-codex-host"},
        "resources": {"cpuMillis": 2000, "memoryMiB": 4096, "processes": 256, "timeoutSeconds": 5400,
                      "temporaryStorageMiB": 256, "concurrency": 1},
        "network": {
            "attachmentRef": OMNIGENT_EGRESS_PROFILE.network_ref,
            "egressProfileRef": OMNIGENT_EGRESS_PROFILE.ref,
        },
        "workspace": {"allowedClasses": ["workflow"], "repositoryMutation": True,
                      "mountClasses": ["workspace", "oauth_home", "omnigent_state", "skills_tools", "artifacts", "cache"],
                      "runtimeUid": 1000, "runtimeGid": 1000},
        "providerProfile": {"compatibleProviders": ["codex"], "queueWhenBusy": True},
        "session": {"create": True, "firstMessage": "required", "continuation": True, "interruption": True,
                    "cancellation": True, "cleanup": "drain" if host_mode == "static_compose" else "remove"},
        "capture": {"required": True, "artifactClasses": ["events", "snapshots", "workspace"], "maxLogBytes": 10000000, "redaction": "required"},
        "checkpoint": {"capture": True, "resume": True, "branch": True, "publication": "approval", "promotion": "verified"},
        "remediation": {"actions": ["retry", "checkpoint_branch"], "riskTiers": {"retry": "low", "checkpoint_branch": "medium"},
                        "locks": True, "maxActions": 3, "autonomous": False},
        "rag": {"initialScope": "workflow", "followupScope": "session", "collectionRefs": ["workflow-default"],
                "tokenBudget": 8000, "fallback": "deny", "credentialRef": "retrieval-profile"},
        "approvals": {"actions": _bootstrap_approval_actions()},
        "retention": {"days": 30, "deletion": "after-expiry"},
        "rollout": {"cohort": "bootstrap", "gate": "deployment-ready", "diagnostics": True},
    })


async def seed_bootstrap_policies(
    session: AsyncSession,
    *,
    env: Mapping[str, str] | None = None,
    image_resolver: ImageResolver = resolve_bootstrap_image_ref,
) -> list[str]:
    """Idempotently persist the three pre-existing built-in authorities."""

    service = OmnigentPolicyService(session)
    reconciliation_required = False
    for policy_id, *_ in _BOOTSTRAP_POLICY_DEFINITIONS:
        policy = await session.get(OmnigentPolicy, policy_id)
        if policy is None:
            reconciliation_required = True
            break
        if policy.default_version is not None:
            try:
                default_row = await service.get_version(
                    policy_id,
                    policy.default_version,
                )
            except PolicyNotFound:
                default_row = None
            if (
                default_row is not None
                and default_row.state == PolicyState.ACTIVE.value
                and default_row.validation_json.get("valid")
            ):
                continue
        try:
            row = await service.get_version(policy_id, 1)
        except PolicyNotFound:
            reconciliation_required = True
            break
        if (
            row.state != PolicyState.ACTIVE.value
            or not row.validation_json.get("valid")
        ):
            reconciliation_required = True
            break
    if not reconciliation_required:
        return []

    seeded: list[str] = []
    server_image, host_image = await resolve_bootstrap_image_refs(
        env=env, image_resolver=image_resolver
    )
    for policy_id, name, host_mode, profile_ref in _BOOTSTRAP_POLICY_DEFINITIONS:
        document = bootstrap_document(
            host_mode=host_mode,
            execution_profile_ref=profile_ref,
            server_image_ref=server_image,
            host_image_ref=host_image,
        )
        policy = await session.get(OmnigentPolicy, policy_id)
        if policy is None:
            row = await service.create(policy_id=policy_id, name=name, owner_user_id=None, visibility="deployment",
                                       document=document, actor="bootstrap")
        else:
            row = await service.get_version(policy_id, 1)
            if row.state != PolicyState.DRAFT.value or row.validation_json.get("valid"):
                continue
            normalized = normalize_document(document)
            validation, compatibility = validate_policy(document)
            if not validation.get("valid"):
                continue
            row.document_json = normalized
            row.digest = document_digest(normalized)
            row.validation_json = validation
            row.compatibility_json = compatibility
            row.rollout_json = normalized["rollout"]
            service._event(policy_id, 1, "bootstrap_repaired", "bootstrap", {
                "digest": row.digest,
            })
        row.env_fallback_used = False
        await session.commit()
        if row.validation_json.get("valid"):
            await service.transition(
                policy_id=policy_id,
                version=row.version,
                state=PolicyState.ACTIVE,
                actor="bootstrap",
                make_default=True,
            )
        seeded.append(policy_id)
    return seeded


async def bootstrap_policies_ready(session: AsyncSession) -> bool:
    """Return whether every built-in policy has immutable active authority."""

    service = OmnigentPolicyService(session)
    for policy_id, *_ in _BOOTSTRAP_POLICY_DEFINITIONS:
        policy = await session.get(OmnigentPolicy, policy_id)
        if policy is None or policy.default_version is None:
            return False
        try:
            row = await service.get_version(policy_id, policy.default_version)
        except PolicyNotFound:
            return False
        if (
            row.state != PolicyState.ACTIVE.value
            or not row.validation_json.get("valid")
        ):
            return False
    return True
