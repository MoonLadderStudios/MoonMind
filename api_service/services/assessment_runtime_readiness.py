"""Pre-budget assessment checks through the existing container worker owner.

Local credential metadata and external registry credential resolution do not
prove remote validity. Values stay in the existing worker resolver, never in
readiness evidence or scanner diagnostics. No target is contacted or assessment
job booked. Image preparation retains its existing registry authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from api_service.db.models import ManagedSecret
from api_service.services.repository_scanner_readiness import (
    require_assessment_step_readiness,
)
from moonmind.auth.secret_refs import (
    SecretBackend,
    SecretReferenceError,
    parse_secret_ref,
)
from moonmind.config.container_backend_settings import RegistryImageSource
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobBackendError,
    ContainerJobSpec,
    OwnerIdentity,
)
from moonmind.schemas.repository_scanner_models import ImageAssessment, RepositoryScan
from moonmind.security.assessment_preset_inputs import assessment_job_spec
from moonmind.security.workload_credentials import (
    authorize_workload_credentials,
    owner_principal,
)
from moonmind.security.workload_profiles import AdmittedAssessmentScope

_ACTIONS = {
    "invalid_assessment_input": "Review the selected assessment inputs and their admitted scope.",
    "assessment_authority_unavailable": "Review the current assessment authorization and credential grants before retrying.",
    "feed_cache_unavailable": "Prepare the selected scanner feed cache with the supported feed refresh or offline import journey.",
    "scanner_image_unavailable": "Review the deployment scanner image source and its image preparation diagnostics.",
    "scanner_probe_unavailable": "The scanner runtime could not be verified. Review the worker and scanner image, then retry.",
    "credential_backend_unavailable": "Cloud and web workload credentials require an active managed db:// secret; select a supported credential before retrying.",
    "credential_unavailable": "The selected credential is missing, disabled, malformed or no longer at its admitted revision. Review its backend, grant and active secret.",
    "credential_resolution_unavailable": "The selected registry credential could not be checked in time. Restore its configured secret backend and retry.",
    "worker_unavailable": "The container worker could not complete readiness checks. Restore the worker and retry.",
}
_PROBE_CODES = frozenset(
    [
        "scanner_executable_unavailable",
        "scanner_version_unavailable",
        "scanner_available",
        "scanner_rules_available",
        "scanner_rules_unavailable",
        "scanner_rule_integrity_failed",
        "feed_snapshot_available",
        "feed_freshness_verified",
        "feed_snapshot_stale",
        "feed_snapshot_incomplete",
        "feed_snapshot_unavailable",
        "feed_snapshot_corrupt",
        "feed_snapshot_incompatible",
        "embedded_checks",
        "java_requirement_source_dependent",
        "invalid_selection",
    ]
)
_COMPONENTS = frozenset(
    ["gitleaks", "opengrep", "trivy", "rules", "feeds", "checks", "java", "selection"]
)
_REGISTRY_CREDENTIAL_TIMEOUT_SECONDS = 15


def readiness_failure(code, *, unavailable=False):
    return {
        "status": "unavailable" if unavailable else "blocked",
        "checks": [],
        "diagnostics": [{"code": code, "action": _ACTIONS[code]}],
    }


def validate_probe_result(raw, *, kind, selection):
    try:
        value = json.loads(raw)
        checks = value["checks"]
        if (
            value.get("schemaVersion") != "scanner-readiness/v1"
            or value.get("status") not in {"ready", "blocked"}
            or not isinstance(checks, list)
            or len(checks) > 16
            or any(
                not isinstance(c, dict)
                or c.get("component") not in _COMPONENTS
                or c.get("status") not in {"ready", "blocked", "unverified"}
                or c.get("code") not in _PROBE_CODES
                for c in checks
            )
        ):
            raise ValueError("invalid scanner evidence")
        field = "repositoryScan" if kind == "repository-scan" else "imageAssessment"
        expected_digest = hashlib.sha256(
            json.dumps(
                {field: selection}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        if value.get("selectionDigest") != expected_digest:
            raise ValueError("wrong readiness selection")
        model = (
            RepositoryScan.model_validate(selection)
            if kind == "repository-scan"
            else ImageAssessment.model_validate(selection)
        )
        scanner = model.scanner if kind == "repository-scan" else "trivy"
        if value["status"] == "ready":
            observed = {(c["component"], c["code"], c["status"]) for c in checks}
            required = {(scanner, "scanner_available", "ready")}
            if kind == "repository-scan":
                required.add(("rules", "scanner_rules_available", "ready"))
            if scanner == "trivy" and (
                model.database_cache_ref
                or (kind == "repository-scan" and "vuln" in model.trivy_scanners)
                or (kind == "image-assessment" and model.operation != "sbom-generate")
            ):
                required.add(("feeds", "feed_snapshot_available", "ready"))
                if (
                    model.require_fresh_feeds
                    or ("feeds", "feed_snapshot_stale", "unverified") not in observed
                ):
                    required.add(("feeds", "feed_freshness_verified", "ready"))
            if any(
                c["component"] in {"gitleaks", "opengrep", "trivy"}
                and c["component"] != scanner
                for c in checks
            ):
                raise ValueError("wrong scanner evidence")
            stale = any(c["code"] == "feed_snapshot_stale" for c in checks)
            fresh = any(c["code"] == "feed_freshness_verified" for c in checks)
            if stale and (fresh or model.require_fresh_feeds):
                raise ValueError("contradictory freshness evidence")
            if not required <= observed:
                raise ValueError("incomplete readiness evidence")
        blocked = [c for c in checks if c["status"] == "blocked"]
        if bool(blocked) != (value["status"] == "blocked"):
            raise ValueError("inconsistent scanner evidence")
        # Keep only the bounded vocabulary, never arbitrary image-authored fields.
        result = {
            "status": value["status"],
            "checks": [
                {k: c[k] for k in ("component", "status", "code")} for c in checks
            ],
            "diagnostics": [],
        }
        for check in blocked:
            component = check["component"]
            action = (
                "Install the selected licensed rule files in the deployment scanner image and verify their digests."
                if component == "rules"
                else (
                    "Refresh or import the selected scanner feed snapshot and retry."
                    if component in {"feeds", "checks", "java"}
                    else "Prepare a supported scanner image containing the selected executable and readiness helper."
                )
            )
            result["diagnostics"].append({"code": check["code"], "action": action})
        return result
    except (ValueError, KeyError, TypeError):
        return readiness_failure("scanner_probe_unavailable", unavailable=True)


async def credential_metadata(session, reference, *, revision=None):
    """Read selected local metadata only, never decrypt or invoke a resolver."""
    try:
        parsed = parse_secret_ref(reference)
    except SecretReferenceError:
        return "blocked"
    if parsed.backend == SecretBackend.ENV:
        return (
            "ready"
            if revision is None
            and bool(str(os.environ.get(parsed.locator) or "").strip())
            else "blocked"
        )
    if parsed.backend != SecretBackend.DB_ENCRYPTED:
        return "unverified"
    # Selecting ManagedSecret itself would decrypt ciphertext on row loading.
    result = await session.execute(
        select(ManagedSecret.status, ManagedSecret.credential_revision).where(
            ManagedSecret.slug == parsed.locator
        )
    )
    row = result.first()
    if row is None or getattr(row.status, "value", row.status) != "active":
        return "blocked"
    if revision is not None and int(row.credential_revision or 1) != revision:
        return "blocked"
    return "ready"


async def check_assessment_runtime_readiness(
    parameters, *, owner, workflow_id, run_id, backend, session
):
    """Recheck admission before any secret metadata or tool-image preparation."""
    from api_service.services.assessment_authorizations import (
        canonical_assessment_scope,
        validate_current_assessment_authorization,
    )
    from api_service.services.registry_authorization import (
        PrivateImageAuthorizationService,
        load_default_authorization_policy,
    )

    if backend is None:
        return readiness_failure("worker_unavailable", unavailable=True)
    settings = backend._settings
    workflow = parameters.get("workflow") or parameters.get("task") or {}
    try:
        identity = OwnerIdentity.model_validate(owner)
        require_assessment_step_readiness(
            workflow,
            settings=settings,
            assessment_scope=parameters.get("assessmentScope"),
            principal=owner_principal(identity),
        )
        scope_value = parameters.get("assessmentScope")
        scope = (
            AdmittedAssessmentScope.model_validate(scope_value)
            if scope_value is not None
            else None
        )
        if scope is not None:
            canonical = await canonical_assessment_scope(session, workflow_id)
            if canonical != scope:
                raise ValueError("scope changed")
            if scope.engagement is not None:
                await validate_current_assessment_authorization(session, scope)
    except (ValueError, PermissionError, TypeError):
        return readiness_failure("assessment_authority_unavailable")
    authorizer = PrivateImageAuthorizationService(load_default_authorization_policy())
    admitted = []
    try:
        # Admit every selection before any metadata or infrastructure work.
        for step in workflow.get("steps", []):
            hint = (
                step.get("annotations", {}).get("securityAssessment")
                if isinstance(step, dict)
                else None
            )
            if hint is None:
                continue
            kind, selected = hint["kind"], hint["admission"]
            job = assessment_job_spec(kind, selected)
            source = settings.image_source(job["imageSourceRef"])
            image_auth = None
            if isinstance(source, RegistryImageSource):
                image_auth = authorizer.authorize_reference(
                    owner=identity,
                    image=source.image,
                    credential_ref=source.registry_credential_ref,
                )
                if not image_auth.authorized:
                    raise PermissionError("tool image denied")
            credentials = []
            if kind in {"cloud-assessment", "web-assessment"}:
                spec = ContainerJobSpec.model_validate(job)
                credentials = authorize_workload_credentials(
                    spec,
                    scope,
                    settings.workload_profiles,
                    principal=owner_principal(identity),
                    now=datetime.now(UTC),
                )
            refs = [
                (d["credentialRef"], d.get("credentialRevision")) for d in credentials
            ]
            target = (
                ImageAssessment.model_validate(selected)
                if kind == "image-assessment"
                else None
            )
            if target is not None and target.target == "registry":
                decision = authorizer.authorize_reference(
                    owner=identity,
                    image=target.image_reference,
                    credential_ref=target.target_credential_ref,
                )
                if not decision.authorized:
                    raise PermissionError("target credential denied")
                if target.target_credential_ref:
                    refs.append((target.target_credential_ref, None))
            admitted.append((kind, selected, job, image_auth, refs))
    except (ValueError, PermissionError, TypeError, KeyError):
        return readiness_failure("assessment_authority_unavailable")
    checks = []
    for kind, _, _, _, refs in admitted:
        for reference, revision in refs:
            if kind in {"cloud-assessment", "web-assessment"}:
                # Match the existing worker's DatabaseSecretResolver. Registry
                # target credentials have a different, broader resolver owner.
                try:
                    supported = (
                        parse_secret_ref(reference).backend
                        == SecretBackend.DB_ENCRYPTED
                    )
                except SecretReferenceError:
                    supported = False
                if not supported:
                    return readiness_failure("credential_backend_unavailable")
            status = await credential_metadata(session, reference, revision=revision)
            if status == "blocked":
                return readiness_failure("credential_unavailable")
            component, code = "credentialMetadata", "active_revision_present"
            if status == "unverified":
                # Registry Exec/Vault references have no local presence metadata.
                # Reuse the worker's authorized resolver, discarding plaintext;
                # this performs no registry login or target request. All selected
                # grants above must pass before any resolver can run.
                try:
                    async with asyncio.timeout(_REGISTRY_CREDENTIAL_TIMEOUT_SECONDS):
                        await backend._resolve_credential(reference)
                except TimeoutError:
                    return readiness_failure(
                        "credential_resolution_unavailable", unavailable=True
                    )
                except ContainerJobBackendError:
                    return readiness_failure("credential_unavailable")
                status, component, code = (
                    "ready",
                    "credentialResolution",
                    "credential_resolvable",
                )
            checks.extend(
                [
                    {
                        "component": component,
                        "status": status,
                        "code": code,
                    },
                    {
                        "component": "credentialValidity",
                        "status": "unverified",
                        "code": "not_probed",
                    },
                ]
            )
    for kind, selected, job, authorization, _ in admitted:
        nonce = uuid.uuid4().hex
        # This is infrastructure preparation only. It intentionally contains no
        # assessment, target, workload environment, attachment or workspace.
        request = ContainerJobActivityRequest.model_validate(
            {
                "jobId": "container-job:" + nonce,
                "ownershipToken": nonce,
                "owner": identity.model_dump(by_alias=True),
                "registryAuthorization": (
                    authorization.model_dump(by_alias=True) if authorization else None
                ),
                "request": {
                    "idempotencyKey": nonce,
                    "source": {
                        "source": "workflow",
                        "workflowId": workflow_id,
                        "runId": run_id,
                    },
                    "spec": {
                        "imageSourceRef": job["imageSourceRef"],
                        "inputAttachments": [],
                        "resources": {
                            "cpuMillis": 1000,
                            "memoryMiB": 1024,
                            "pids": 128,
                        },
                        "timeoutSeconds": 90,
                    },
                },
            }
        )
        result = await backend.assessment_readiness(
            request, kind=kind, selection=selected, caches=job.get("caches", [])
        )
        if result["status"] != "ready":
            return result
        checks.extend(result["checks"])
    return {"status": "ready", "diagnostics": [], "checks": checks}
