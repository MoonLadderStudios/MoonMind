"""Exact organisational artifact inputs for the existing execution admission owner."""

from collections.abc import Mapping

from moonmind.security.frameworks import OrganisationalEvidenceRequest


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
        pack = inputs.get("frameworkPack", inputs.get("framework_pack")) or {}
        if not isinstance(control, Mapping) or not isinstance(pack, Mapping):
            raise TypeError("invalid assessment evidence plan")
        requirements = control.get("requirements", [])
        choices = pack.get("requirementChoices", pack.get("requirement_choices", {}))
        if (
            not isinstance(requirements, list)
            or not isinstance(choices, Mapping)
            or len(requirements) + len(choices) > 64
        ):
            raise ValueError("invalid assessment requirement collection")
        framework = inputs.get("framework") or {}
        builtin_rows = []
        if isinstance(framework, Mapping) and framework.get(
            "packId", framework.get("pack_id")
        ) in {"iso-27001-readiness", "soc2-readiness"}:
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
        for row in [*requirements, *choices.values(), *builtin_rows]:
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
    if len(refs) > 64:
        raise ValueError("organisational evidence exceeds reference bound")
    return refs
