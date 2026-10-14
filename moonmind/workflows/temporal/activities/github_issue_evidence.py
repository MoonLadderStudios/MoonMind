"""Persist exact-candidate CI bytes for the existing issue continuation owner.

The registered Activity supplies its real execution identity and admitted GitHub
service. This module neither decides acceptance nor schedules/mutates CI jobs.
Large/untrusted bytes stay in TemporalArtifactService; workflow history receives
compact identities and references for the existing verifier materializer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from moonmind.workflows.adapters.github_service import GitHubService


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


async def collect_github_issue_evidence(
    payload: Mapping[str, Any],
    *,
    artifact_service: Any = None,
    service: GitHubService | None = None,
) -> dict[str, Any]:
    """Collect bytes, or cheaply recheck the exact candidate before publication.

    The Activity dispatcher must replace ``workflowId``/``admittedWorkflowId``
    with its executing workflow identity; user payloads cannot select authority.
    ``semanticDigest`` excludes generated artifact IDs, so repeating a collection
    cannot manufacture progress or spend another verifier/implementation attempt.
    """
    workflow_id = str(payload.get("workflowId") or "").strip()
    if not workflow_id:
        raise ValueError("The executing workflow identity is required")
    admitted_id = str(payload.get("admittedWorkflowId") or workflow_id)
    if admitted_id != workflow_id:
        raise ValueError(
            "Evidence collection must use the executing workflow admission"
        )
    action = str(payload.get("action") or "collect")
    if action not in {"collect", "verify_candidate"}:
        raise ValueError("Unsupported evidence-reader action")
    github = service or GitHubService()
    observed = await github.read_actions_evidence(
        str(payload.get("repository") or ""),
        payload.get("prNumber"),
        str(payload.get("headSha") or ""),
        admitted_workflow_id=admitted_id,
        workflow_identities=payload.get("workflowIdentities") or (),
        artifact_names=payload.get("artifactNames") or (),
        verify_candidate_only=action == "verify_candidate",
    )
    files = observed.pop("files", [])
    result = {
        **observed,
        "evidenceRefs": [],
        "evidenceSet": {},
        "artifactRef": None,
        "evidenceSetDigest": None,
    }
    if action == "verify_candidate":
        return result
    if artifact_service is None:
        from api_service.db.base import async_session_maker
        from moonmind.workflows.temporal.artifacts import (
            TemporalArtifactRepository,
            TemporalArtifactService,
        )

        async with async_session_maker() as session:
            return await _persist_evidence(
                payload,
                result,
                files,
                TemporalArtifactService(TemporalArtifactRepository(session)),
            )
    return await _persist_evidence(payload, result, files, artifact_service)


async def _persist_evidence(
    payload: Mapping[str, Any],
    result: dict[str, Any],
    files: list[dict[str, Any]],
    service: Any,
) -> dict[str, Any]:
    from moonmind.workflows.temporal.artifacts import ExecutionRef

    workflow_id = str(payload["workflowId"])
    run_id = str(payload.get("runId") or "").strip()
    if not run_id:
        raise ValueError("The executing Temporal run identity is required")
    principal = f"workflow:{workflow_id}"
    namespace = str(payload.get("namespace") or "moonmind")

    async def persist(
        body: bytes, name: str, metadata: dict[str, Any], content_type: str
    ) -> str:
        artifact, _ = await service.put_content_addressed_payload_complete(
            principal=principal,
            payload=body,
            content_type=content_type,
            scope="github-ci-" + hashlib.sha256(workflow_id.encode()).hexdigest()[:32],
            link=ExecutionRef(
                namespace=namespace,
                workflow_id=workflow_id,
                run_id=run_id,
                link_type="github.issue.ci_evidence",
                label=name,
                created_by_activity_type="github.issue.collect_evidence",
            ),
            metadata_json={
                "name": name,
                "artifact_type": "github.issue.ci_evidence",
                **metadata,
            },
        )
        return f"artifact://{artifact.artifact_id}"

    inventory = []
    for item in files:
        body = item["payload"]
        metadata = {key: value for key, value in item.items() if key != "payload"}
        # Verify at the store boundary too: fake/accidentally transformed service
        # results must not advertise a digest different from the retained bytes.
        if (
            not isinstance(body, bytes)
            or hashlib.sha256(body).hexdigest() != item["sha256"]
        ):
            raise ValueError("Evidence payload integrity mismatch before persistence")
        source = str(item["artifactId"]) if "artifactId" in item else "diagnostics"
        name = f"ci/{item['runId']}/{item['runAttempt']}/{source}/{item['path']}"
        ref = await persist(
            body,
            name,
            {
                **metadata,
                "repository": result["repository"],
                "headSha": result["headSha"],
            },
            "application/octet-stream",
        )
        result["evidenceRefs"].append(ref)
        result["evidenceSet"][ref] = "sha256:" + item["sha256"]
        inventory.append({**metadata, "artifactRef": ref})
    if result["evidenceSet"]:
        result["evidenceSetDigest"] = (
            "sha256:" + hashlib.sha256(_json_bytes(result["evidenceSet"])).hexdigest()
        )
    manifest = {
        "schemaVersion": "github-actions-evidence/v1",
        **result,
        "files": inventory,
    }
    result["artifactRef"] = await persist(
        _json_bytes(manifest),
        "ci/evidence-manifest.json",
        {
            "repository": result["repository"],
            "headSha": result["headSha"],
            "semanticDigest": result.get("semanticDigest"),
        },
        "application/json",
    )
    return result
