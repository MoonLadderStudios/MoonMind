"""Preset input validation delegates to the existing native job contracts.

Validation provides no target, credential, network or job authority. Admission
and execution independently enforce those boundaries.
"""

from pydantic import ValidationError

from moonmind.schemas.repository_scanner_models import (
    CloudAssessment,
    EndpointAssessment,
    ImageAssessment,
    RepositoryScan,
    WebAssessment,
)

ASSESSMENT_INPUT_CONTRACTS = {
    "repository-scan": RepositoryScan,
    "image-assessment": ImageAssessment,
    "cloud-assessment": CloudAssessment,
    "web-assessment": WebAssessment,
    "endpoint-assessment": EndpointAssessment,
}

#: Operator next action for each pre-budget readiness diagnostic. The runtime
#: readiness gate reports them; security plans cite them for their gaps.
READINESS_ACTIONS = {
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


def assessment_job_spec(contract: str, value: dict) -> dict:
    """Compile workload intent with the existing native job owner.

    This supplies no session, workspace, scope or credential authority. The
    shipped CLI stamps its caller context and the API independently admits it.
    """
    from moonmind.security.cloud_assessments import build_cloud_assessment_job
    from moonmind.security.endpoint_assessments import build_endpoint_assessment_job
    from moonmind.security.image_assessments import build_image_assessment_job
    from moonmind.security.repository_scanners import build_scanner_job
    from moonmind.security.web_assessments import build_web_assessment_job

    builders = {
        "repository-scan": build_scanner_job,
        "image-assessment": build_image_assessment_job,
        "cloud-assessment": build_cloud_assessment_job,
        "web-assessment": build_web_assessment_job,
        "endpoint-assessment": build_endpoint_assessment_job,
    }
    model = ASSESSMENT_INPUT_CONTRACTS[contract].model_validate(value)
    return builders[contract](model)


INPUT_HELP = {
    "repository-scan": "Select a supported scanner and output directory; omit rules for bundled rules or supply a nonempty licensed selection. Database identity requires a cache.",
    "image-assessment": "Registry targets need imageReference and egressPolicyRef; archives/SBOMs need inputArtifactRef. Vulnerability matching needs databaseCacheRef and databaseDigest; SBOM generation does not.",
    "cloud-assessment": "Select AWS, account, credentialRef, regions and egressPolicyRef, with exactly one nonempty checks or services selection.",
    "web-assessment": "API mode needs apiDefinition; active mode needs activeStrength. Admit all start, authentication and definition endpoint paths in routes, outside exclusions.",
    "endpoint-assessment": "Name the endpoint asset and inputArtifactRef. A rootfs or image target also needs a shipped datastream (ssg-<product>-ds.xml) and its XCCDF profile; imported ARF or XCCDF results need neither. No remediation, fix or command input exists.",
}


def input_diagnostics(model, exc: ValidationError, help_text: str) -> list[str]:
    """Field-addressed native contract errors that never echo input values."""
    # Do not echo input values (including credentials or malformed secrets).
    errors = exc.errors(include_input=False, include_context=False, include_url=False)
    fields = {field.alias or name for name, field in model.model_fields.items()}
    messages = []
    for error in errors:
        first = error["loc"][0] if error["loc"] else None
        location = str(first) if first in fields else "assessment"
        # Custom validators can interpolate supplied values. Neither their
        # text nor an unknown field's name belongs in a public diagnostic.
        detail = help_text if error["type"] == "value_error" else error["msg"]
        messages.append(f"{location}: {detail}")
    return messages


def validate_assessment_input(contract: str, value: dict) -> dict:
    model = (
        ASSESSMENT_INPUT_CONTRACTS.get(contract) if isinstance(contract, str) else None
    )
    if model is None:
        raise ValueError("Unsupported assessment input contract; refresh the preset.")
    try:
        admitted = model.model_validate(value)
    except ValidationError as exc:
        raise ValueError(
            "; ".join(input_diagnostics(model, exc, INPUT_HELP[contract]))
        ) from exc
    resolved = admitted.model_dump(mode="json", by_alias=True, exclude_none=True)
    # Native reports omit false compatibility flags. Authored preset inputs must
    # retain explicit false so a saved rerun cannot apply a new true default.
    for name, field in model.model_fields.items():
        alias = field.alias or name
        if (alias in value or name in value) and getattr(admitted, name) is False:
            resolved[alias] = False
    return resolved
