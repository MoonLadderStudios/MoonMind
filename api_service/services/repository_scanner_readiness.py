"""Read-only deployment configuration, never a live scanner health claim."""

from collections.abc import Mapping
from datetime import UTC
from typing import Any

from moonmind.config.container_backend_settings import ContainerBackendSettings
from moonmind.security.scanner_feeds import (
    FEED_CACHE_TARGET,
    FEED_HOSTS,
    feed_policy_admits,
)


def repository_scanner_readiness(settings: ContainerBackendSettings) -> dict:
    """Project configured defaults without pulling images or inspecting volumes.

    The API does not run on the container worker. Image contents, feed freshness,
    and network reachability remain unverified until execution evidence exists.
    """
    source_ref = "repository-security"
    image = next(
        (s for s in settings.image_sources if s.source_ref == source_ref), None
    )
    caches = {s.cache_ref: s for s in settings.cache_sources}
    reader, writer = caches.get("scanner-db"), caches.get("scanner-feeds")
    cache_configured = bool(
        reader
        and writer
        and reader.read_only
        and not writer.read_only
        and reader.target == "/opt/moonmind-scanner-db"
        and writer.target == FEED_CACHE_TARGET
        and reader.volume_name == writer.volume_name
    )
    policies = [
        {
            "policyRef": policy.policy_ref,
            "workflowIds": list(policy.allowed_workflow_ids),
        }
        for policy in settings.egress_policies
        if source_ref in policy.allowed_image_source_refs
        and feed_policy_admits(policy)
        and FEED_HOSTS <= {d.host for d in policy.destinations}
    ]
    diagnostics = []
    if not settings.enabled or not settings.endpoint:
        diagnostics.append(
            {
                "code": "container_backend_unavailable",
                "action": "Enable the container backend and configure its worker endpoint.",
            }
        )
    if image is None:
        diagnostics.append(
            {
                "code": "missing_image_source",
                "action": "Configure the repository-security image source in the deployment container backend settings.",
            }
        )
    if not cache_configured:
        diagnostics.append(
            {
                "code": "feed_cache_unavailable",
                "action": "Configure scanner-db read-only at /opt/moonmind-scanner-db and scanner-feeds writable at /opt/moonmind-scanner-feeds on the same named volume.",
            }
        )
    if not policies:
        diagnostics.append(
            {
                "code": "refresh_egress_unavailable",
                "action": "Authorize a feed-only HTTPS egress policy for repository-security and an explicit refresh workflow, covering ghcr.io and pkg-containers.githubusercontent.com. Offline feed import remains available.",
            }
        )
    return {
        "status": "unverified",
        "imageSourceRef": source_ref,
        "sourceImage": image.image if image else None,
        "scanners": ["gitleaks", "opengrep", "trivy"],
        "ruleSelection": "image-default",
        "ruleCoverage": "Bundled default: Python eval/shell rules and JavaScript/TypeScript eval rules. Custom images may differ; execution records the installed rules.",
        "databaseCacheRef": "scanner-db",
        "refreshCacheRef": "scanner-feeds",
        "feedCacheConfigured": cache_configured,
        "feedSnapshot": "unverified",
        "refreshPolicies": policies,
        "diagnostics": diagnostics,
        "verificationNote": "Configuration only. Image contents, installed rules, feed snapshot freshness and refresh connectivity are Unverified until actual execution evidence is available. No scan or refresh is started here.",
    }


def assessment_input_readiness(
    settings: ContainerBackendSettings, kind: str, value: dict
) -> dict:
    """Project selected native inputs without issuing authority or spending jobs.

    Only deployment configuration is observable here. A supplied credential,
    rule digest or feed digest is a selection, never proof of runtime health.
    ContainerJob admission and native runners retain their independent checks.
    """
    from pydantic import ValidationError

    from moonmind.security.assessment_preset_inputs import ASSESSMENT_INPUT_CONTRACTS
    from moonmind.security.cloud_assessments import build_cloud_assessment_job
    from moonmind.security.image_assessments import build_image_assessment_job
    from moonmind.security.repository_scanners import build_scanner_job
    from moonmind.security.web_assessments import build_web_assessment_job

    diagnostics = []
    runtime = {}

    def diagnostic(code, field, action):
        diagnostics.append({"code": code, "field": field, "action": action})

    def result():
        return {
            "status": "blocked" if diagnostics else "unverified",
            "diagnostics": diagnostics,
            "runtimeChecks": runtime,
            "verificationNote": (
                "Configuration only. Runtime checks remain Unverified until worker "
                "execution evidence exists. This check starts no assessment, consumes "
                "no assessment budget and grants no target or credential authority."
            ),
        }

    model = ASSESSMENT_INPUT_CONTRACTS.get(kind)
    if model is None:
        diagnostic(
            "invalid_assessment_input",
            "assessment",
            "Select a supported assessment input contract.",
        )
        return result()
    try:
        assessment = model.model_validate(value)
    except ValidationError as exc:
        # Native custom validators can include input in their messages. Emit
        # only schema-owned field locations, never messages, context or values.
        for error in exc.errors(
            include_input=False, include_context=False, include_url=False
        ):
            known_fields = {name for name in model.model_fields} | {
                field.alias for field in model.model_fields.values()
            }
            first = error["loc"][0] if error["loc"] else None
            location = str(first) if first in known_fields else ""
            diagnostic(
                "invalid_assessment_input",
                "assessment" + ("." + location if location else ""),
                "Supply a valid value required by the selected native assessment contract.",
            )
        return result()
    builders = {
        "repository-scan": build_scanner_job,
        "image-assessment": build_image_assessment_job,
        "cloud-assessment": build_cloud_assessment_job,
        "web-assessment": build_web_assessment_job,
    }
    job = builders[kind](assessment)
    source_ref = job["imageSourceRef"]
    runtime["scannerImage"] = "unverified"
    if not settings.enabled or not settings.endpoint:
        diagnostic(
            "container_backend_unavailable",
            "assessment",
            "Enable the container backend and configure its worker endpoint.",
        )
    if not any(source.source_ref == source_ref for source in settings.image_sources):
        diagnostic(
            "missing_image_source",
            "assessment",
            f"Configure the {source_ref} image source in deployment settings.",
        )
    for mount in job.get("caches", []):
        if not any(
            source.cache_ref == mount["cacheRef"]
            and source.target == mount["target"]
            and source.read_only == mount["readOnly"]
            for source in settings.cache_sources
        ):
            diagnostic(
                "feed_cache_unavailable",
                "assessment.databaseCacheRef",
                "Configure the selected read-only scanner database cache at its native mount target.",
            )
        runtime["feedFreshness"] = "unverified"
    if kind == "repository-scan" and (
        assessment.scanner == "opengrep" or assessment.rules
    ):
        runtime["licensedRules"] = "unverified"
    if job.get("inputAttachments"):
        runtime["capturedInput"] = "unverified"
    egress = job.get("egressPolicyRef")
    if egress:
        if not any(
            policy.policy_ref == egress
            and source_ref in policy.allowed_image_source_refs
            for policy in settings.egress_policies
        ):
            diagnostic(
                "egress_policy_unavailable",
                "assessment.egressPolicyRef",
                "Configure an egress policy for the selected scanner image and target. Workflow and target authorization are checked again at job admission.",
            )
        runtime["targetAccess"] = "unverified"
    if kind == "cloud-assessment":
        runtime["accountCredential"] = "unverified"
    elif kind == "web-assessment" and assessment.identity is not None:
        runtime["identityCredential"] = "unverified"
    elif kind == "image-assessment" and assessment.target_credential_ref:
        runtime["registryCredential"] = "unverified"
    return result()


def require_assessment_step_readiness(
    workflow: Mapping[str, Any],
    *,
    settings: ContainerBackendSettings | None = None,
    assessment_scope=None,
    principal: str | None = None,
) -> list[dict]:
    """Validate compiler hints at plan admission; never create assessment authority.

    Returns configuration observations for callers that retain them. Native job
    admission rechecks authority against current grants before reserving jobs.
    """
    from datetime import datetime

    from moonmind.config.container_backend_settings import (
        ContainerBackendConfigError,
        resolve_container_backend_settings,
    )
    from moonmind.schemas.container_job_models import ContainerJobSpec
    from moonmind.schemas.repository_scanner_models import (
        CloudAssessment,
        WebAssessment,
    )
    from moonmind.security.assessment_preset_inputs import validate_assessment_input
    from moonmind.security.cloud_assessments import build_cloud_assessment_job
    from moonmind.security.web_assessments import build_web_assessment_job
    from moonmind.security.workload_credentials import authorize_workload_credentials
    from moonmind.security.workload_profiles import (
        AdmittedAssessmentScope,
        resolved_job_within_scope,
    )

    observations = []
    steps = workflow.get("steps")
    if not isinstance(steps, list):
        return observations
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        annotations = step.get("annotations")
        if (
            not isinstance(annotations, Mapping)
            or "securityAssessment" not in annotations
        ):
            continue
        field = f"steps.{index}.assessment"
        hint = annotations["securityAssessment"]
        skill = step.get("skill")
        if not isinstance(hint, Mapping) or not isinstance(skill, Mapping):
            raise ValueError(f"{field}: Select a valid security assessment preset.")  # noqa: TRY004 -- admission maps ValueError to a safe authoring response
        args = skill.get("args")
        if (
            skill.get("id") != "security-review"
            or hint.get("phase") != "collect"
            or not isinstance(args, Mapping)
        ):
            raise ValueError(
                f"{field}: Assessment readiness must match the security-review collection step."
            )
        kind = hint.get("kind")
        try:
            admitted = validate_assessment_input(kind, hint.get("admission"))
            selected = validate_assessment_input(kind, args.get("assessment"))
        except (ValueError, TypeError):
            raise ValueError(
                f"{field}: Supply valid inputs for the selected native assessment contract."
            ) from None
        if admitted != selected:
            raise ValueError(
                f"{field}: Assessment readiness selection differs from the Skill arguments."
            )
        if settings is None:
            try:
                settings = resolve_container_backend_settings()
            except ContainerBackendConfigError:
                raise ValueError(
                    f"{field}: Review deployment container backend configuration; runtime readiness is Unverified."
                ) from None
        observation = assessment_input_readiness(settings, kind, selected)
        if observation["diagnostics"]:
            raise ValueError(
                "; ".join(
                    f"steps.{index}.{item['field']}: {item['code']}: {item['action']}"
                    for item in observation["diagnostics"]
                )
            )
        live = kind in {"cloud-assessment", "web-assessment"}
        if live and assessment_scope is None:
            raise ValueError(
                f"{field}: Review and admit the target assessment scope before starting cloud or web collection."
            )
        if not live and assessment_scope is not None:
            raise ValueError(
                f"{field}: Repository and image collection cannot run in a scoped cloud or live-target session; use a separate unscoped workflow."
            )
        if live:
            try:
                scope = AdmittedAssessmentScope.model_validate(assessment_scope)
                model, builder = (
                    (CloudAssessment, build_cloud_assessment_job)
                    if kind == "cloud-assessment"
                    else (WebAssessment, build_web_assessment_job)
                )
                job = builder(model.model_validate(selected))
                spec = ContainerJobSpec.model_validate(job)
                resolved_job_within_scope(spec, scope, settings.workload_profiles)
                if principal is None and (
                    spec.cloud_assessment is not None
                    or spec.web_assessment.identity is not None
                ):
                    raise ValueError("missing admission principal")
                authorize_workload_credentials(
                    spec,
                    scope,
                    settings.workload_profiles,
                    principal=principal,
                    now=datetime.now(UTC),
                )
            except (ValueError, PermissionError, TypeError):
                raise ValueError(
                    f"{field}: Review the admitted target, limits, network and credential grants; they must cover the selected assessment and its full job budget."
                ) from None
        observations.append(observation)
    return observations
