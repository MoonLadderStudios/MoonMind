"""Exact organisational artifact inputs for the existing execution admission owner."""

from collections.abc import Mapping

from moonmind.security.frameworks import (
    MAX_CONTROL_EVIDENCE_REFS,
    MAX_CONTROL_REQUIREMENTS,
    MAX_PACK_REQUIREMENTS,
    AssessmentInputError,
    OrganisationalEvidenceRequest,
    selected_framework_packs,
)


def _installed(pack_id) -> bool:
    from moonmind.security.framework_registry import INSTALLED_PACK_IDS

    return pack_id in INSTALLED_PACK_IDS


def _profile_refs(selection: Mapping) -> list[str]:
    """Current revisions an installed pack's behavior reviews from its profile.

    An invalid profile admits nothing here; assessment validation rejects it.
    """
    from pydantic import ValidationError

    from moonmind.security.framework_registry import installed_pack, pack_behavior
    from moonmind.security.pack_readiness import profile_model

    pack_id = selection.get("packId", selection.get("pack_id"))
    profile = selection.get("profile")
    if not _installed(pack_id) or not isinstance(profile, Mapping):
        return []
    pack = installed_pack(pack_id)[1]
    try:
        inputs = profile_model(pack).model_validate(profile)
    except ValidationError:
        return []
    return list(pack_behavior(pack).organisational_refs(inputs))


def organisational_evidence_refs(task: Mapping) -> list[str]:
    """No artifact-string discovery: only current revisions of explicit security.assess tools."""
    refs = []
    steps = task.get("steps") or []
    if not isinstance(steps, list):
        return []
    for step in [task, *steps]:
        if not isinstance(step, Mapping):
            continue
        tool = step.get("tool")
        if (
            not isinstance(tool, Mapping)
            or (tool.get("name") or tool.get("id")) != "security.assess"
        ):
            continue
        inputs = tool.get("inputs", {})
        if not isinstance(inputs, Mapping):
            raise TypeError("invalid assessment inputs")
        control = (
            inputs.get("controlAssessment", inputs.get("control_assessment")) or {}
        )
        packs = selected_framework_packs(
            inputs.get("frameworkPack", inputs.get("framework_pack")),
            inputs.get("frameworkPacks", inputs.get("framework_packs", [])),
        )
        if not isinstance(control, Mapping):
            raise TypeError("invalid controlAssessment")
        requirements = control.get("requirements", [])
        if not isinstance(requirements, list):
            raise ValueError("invalid controlAssessment.requirements")
        selected_rows = list(requirements)
        for index, pack in enumerate(packs):
            if not isinstance(pack, Mapping):
                raise TypeError(f"invalid frameworkPacks[{index}]")
            choices = pack.get(
                "requirementChoices", pack.get("requirement_choices", {})
            )
            if not isinstance(choices, Mapping) or len(choices) > MAX_PACK_REQUIREMENTS:
                raise AssessmentInputError(
                    f"frameworkPacks[{index}].requirementChoices exceeds the {MAX_PACK_REQUIREMENTS} choice bound or is invalid"
                )
            selected_rows.extend(choices.values())
        if len(selected_rows) > MAX_CONTROL_REQUIREMENTS:
            raise AssessmentInputError(
                f"requirements exceeds the {MAX_CONTROL_REQUIREMENTS} aggregate bound"
            )
        framework = inputs.get("framework") or {}
        builtin_rows = []
        if isinstance(framework, Mapping) and _installed(
            framework.get("packId", framework.get("pack_id"))
        ):
            # A saved framework.packId request keeps documentary evidence in
            # its system inputs until the request boundary normalizes it.
            system = framework.get("systemInputs", framework.get("system_inputs", {}))
            if not isinstance(system, Mapping):
                raise TypeError("invalid built-in framework system inputs")
            evidence = system.get(
                "requirementEvidence", system.get("requirement_evidence", {})
            )
            if not isinstance(evidence, Mapping) or len(evidence) > 256:
                raise ValueError("invalid built-in framework evidence collection")
            builtin_rows = [
                {"organisationalEvidence": requests} for requests in evidence.values()
            ]
        for pack in packs:
            refs.extend(_profile_refs(pack))
        for row in [*selected_rows, *builtin_rows]:
            if not isinstance(row, Mapping):
                raise TypeError("invalid assessment requirement")
            requests = row.get(
                "organisationalEvidence", row.get("organisational_evidence", [])
            )
            if not isinstance(requests, list) or len(requests) > 32:
                raise ValueError("invalid organisational evidence collection")
            for request in requests:
                parsed = OrganisationalEvidenceRequest.model_validate(request)
                refs.extend(revision.artifact_ref for revision in parsed.revisions[-1:])
    refs = list(dict.fromkeys(refs))
    if len(refs) > MAX_CONTROL_EVIDENCE_REFS:
        raise AssessmentInputError(
            f"organisationalEvidence exceeds the {MAX_CONTROL_EVIDENCE_REFS} reference bound"
        )
    return refs
