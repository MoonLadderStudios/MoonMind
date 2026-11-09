"""Preset input validation delegates to the existing native job contracts.

Validation provides no target, credential, network or job authority. Admission
and execution independently enforce those boundaries.
"""

from pydantic import ValidationError

from moonmind.schemas.repository_scanner_models import (
    CloudAssessment,
    ImageAssessment,
    RepositoryScan,
    WebAssessment,
)

ASSESSMENT_INPUT_CONTRACTS = {
    "repository-scan": RepositoryScan,
    "image-assessment": ImageAssessment,
    "cloud-assessment": CloudAssessment,
    "web-assessment": WebAssessment,
}


def assessment_job_spec(contract: str, value: dict) -> dict:
    """Compile workload intent with the existing native job owner.

    This supplies no session, workspace, scope or credential authority. The
    shipped CLI stamps its caller context and the API independently admits it.
    """
    from moonmind.security.cloud_assessments import build_cloud_assessment_job
    from moonmind.security.image_assessments import build_image_assessment_job
    from moonmind.security.repository_scanners import build_scanner_job
    from moonmind.security.web_assessments import build_web_assessment_job

    builders = {
        "repository-scan": build_scanner_job,
        "image-assessment": build_image_assessment_job,
        "cloud-assessment": build_cloud_assessment_job,
        "web-assessment": build_web_assessment_job,
    }
    model = ASSESSMENT_INPUT_CONTRACTS[contract].model_validate(value)
    return builders[contract](model)


_INPUT_HELP = {
    "repository-scan": "Select a supported scanner and output directory; omit rules for bundled rules or supply a nonempty licensed selection. Database identity requires a cache.",
    "image-assessment": "Registry targets need imageReference and egressPolicyRef; archives/SBOMs need inputArtifactRef. Vulnerability matching needs databaseCacheRef and databaseDigest; SBOM generation does not.",
    "cloud-assessment": "Select AWS, account, credentialRef, regions and egressPolicyRef, with exactly one nonempty checks or services selection.",
    "web-assessment": "API mode needs apiDefinition; active mode needs activeStrength. Admit all start, authentication and definition endpoint paths in routes, outside exclusions.",
}


def validate_assessment_input(contract: str, value: dict) -> dict:
    model = (
        ASSESSMENT_INPUT_CONTRACTS.get(contract) if isinstance(contract, str) else None
    )
    if model is None:
        raise ValueError("Unsupported assessment input contract; refresh the preset.")
    try:
        admitted = model.model_validate(value)
    except ValidationError as exc:
        # Do not echo input values (including credentials or malformed secrets).
        errors = exc.errors(
            include_input=False, include_context=False, include_url=False
        )
        messages = []
        for error in errors:
            first = error["loc"][0] if error["loc"] else None
            fields = {field.alias or name for name, field in model.model_fields.items()}
            location = str(first) if first in fields else "assessment"
            # Custom validators can interpolate supplied values. Neither their
            # text nor an unknown field's name belongs in a public diagnostic.
            detail = (
                _INPUT_HELP[contract]
                if error["type"] == "value_error"
                else error["msg"]
            )
            messages.append(f"{location}: {detail}")
        message = "; ".join(messages)
        raise ValueError(message) from exc
    resolved = admitted.model_dump(mode="json", by_alias=True, exclude_none=True)
    # Native reports omit false compatibility flags. Authored preset inputs must
    # retain explicit false so a saved rerun cannot apply a new true default.
    for name, field in model.model_fields.items():
        alias = field.alias or name
        if (alias in value or name in value) and getattr(admitted, name) is False:
            resolved[alias] = False
    return resolved
