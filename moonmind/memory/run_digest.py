"""Plane B task-history run digest models and builder.

MoonLadderStudios/MoonMind#4109: digest construction is a pure projection of
the authoritative terminal execution record. There is no vector indexing,
embedding, or semantic recall here; durable persistence is owned by the
Temporal artifact/finalization path, which stores the digest as a bounded
``output.summary`` artifact. The resolved ``mem0ai`` SDK mandatorily depends
on ``qdrant-client`` (see poetry.lock), so hosted Mem0 cannot satisfy the
Qdrant-free requirement and is retired rather than kept as a dormant adapter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from moonmind.workflows.executions.repository_contract import (
    repository_name_from_value,
)

RUN_DIGEST_SCHEMA_VERSION = "v1"
RUN_DIGEST_RECORD_KIND = "run_digest"
RUN_DIGEST_TRUST_CLASS = "derived"
RUN_DIGEST_ARTIFACT_SCHEMA_VERSION = "run_digest_artifact/v1"
_MAX_TEXT = 500
_MAX_LIST_ITEMS = 8


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


class RunDigestEvidence(BaseModel):
    """Evidence pointers retained with a derived run digest."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    agent_run_id: str | None = Field(None, alias="agentRunId")
    summary_artifact_ref: str | None = Field(None, alias="summaryArtifactRef")
    input_ref: str | None = Field(None, alias="inputRef")
    plan_ref: str | None = Field(None, alias="planRef")
    manifest_ref: str | None = Field(None, alias="manifestRef")
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple, alias="artifactRefs")
    commits: tuple[str, ...] = Field(default_factory=tuple, alias="commits")
    pull_request_url: str | None = Field(None, alias="pullRequestUrl")


class RunDigest(BaseModel):
    """Short structured summary of one terminal agent run."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: str = Field(RUN_DIGEST_SCHEMA_VERSION, alias="schemaVersion")
    record_kind: str = Field(RUN_DIGEST_RECORD_KIND, alias="recordKind")
    namespace_id: str = Field(..., alias="namespaceId", min_length=1)
    repo: str | None = None
    security_scope: str = Field(..., alias="securityScope", min_length=1)
    workflow_type: str = Field(..., alias="workflowType", min_length=1)
    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    state: str = Field(..., min_length=1)
    close_status: str | None = Field(None, alias="closeStatus")
    intent: str = Field(..., min_length=1)
    outcome: str = Field(..., min_length=1)
    key_changes: tuple[str, ...] = Field(default_factory=tuple, alias="keyChanges")
    key_decisions: tuple[str, ...] = Field(default_factory=tuple, alias="keyDecisions")
    gotchas: tuple[str, ...] = Field(default_factory=tuple)
    next_steps: tuple[str, ...] = Field(default_factory=tuple, alias="nextSteps")
    evidence: RunDigestEvidence
    created_at: str = Field(default_factory=_utc_now, alias="createdAt")


def build_run_digest(record: Any) -> RunDigest:
    """Build a compact digest from a terminal execution record.

    Pure constructor: reads only the authoritative relational record (plus its
    memo/parameters/search-attributes evidence pointers) and never touches a
    vector index, embedding provider, or network service.
    """

    memo = _mapping(getattr(record, "memo", None))
    params = _mapping(getattr(record, "parameters", None))
    attrs = _mapping(getattr(record, "search_attributes", None))
    task = _mapping(params.get("task"))
    git = _mapping(task.get("git"))
    publish = _mapping(params.get("publish") or params.get("publishContext"))

    workflow_id = _required_text(getattr(record, "workflow_id", None), "workflow_id")
    run_id = _required_text(getattr(record, "run_id", None), "run_id")
    namespace = _first_text(
        getattr(record, "namespace", None),
        attrs.get("namespace_id"),
        attrs.get("namespace"),
        "default",
    )
    repo = _first_text(
        repository_name_from_value(git.get("repository")),
        repository_name_from_value(task.get("repository")),
        task.get("repo"),
        repository_name_from_value(params.get("repository")),
        params.get("repo"),
        attrs.get("mm_repository"),
        attrs.get("mm_repo"),
        memo.get("repository"),
    )
    state = _enum_text(getattr(record, "state", None))
    close_status = _enum_text(getattr(record, "close_status", None)) or None
    workflow_type = _enum_text(getattr(record, "workflow_type", None)) or "unknown"
    title = _first_text(
        getattr(record, "title", None),
        memo.get("title"),
        task.get("title"),
        task.get("summary"),
        task.get("instructions"),
        params.get("instructions"),
        workflow_id,
    )
    summary = _first_text(memo.get("summary"), params.get("summary"))
    outcome = _compact(
        summary
        or (
            f"Execution reached {close_status or state}."
            if close_status or state
            else "Execution reached a terminal state."
        )
    )

    artifact_refs = _string_tuple(getattr(record, "artifact_refs", None))
    summary_ref = _first_text(
        memo.get("summary_artifact_ref"),
        memo.get("summaryArtifactRef"),
        params.get("summary_artifact_ref"),
        params.get("summaryArtifactRef"),
    )
    agent_run_id = _first_text(
        memo.get("agentRunId"),
        memo.get("agent_run_id"),
        attrs.get("mm_agent_run_id"),
        params.get("agentRunId"),
        params.get("agent_run_id"),
    )
    pull_request_url = _first_text(
        publish.get("pullRequestUrl"),
        publish.get("prUrl"),
        params.get("pullRequestUrl"),
        memo.get("pullRequestUrl"),
    )
    commits = _string_tuple(
        publish.get("commits")
        or publish.get("commitShas")
        or params.get("commits")
        or params.get("commitShas")
    )
    evidence = RunDigestEvidence(
        workflowId=workflow_id,
        runId=run_id,
        agentRunId=agent_run_id,
        summaryArtifactRef=summary_ref,
        inputRef=_first_text(getattr(record, "input_ref", None)),
        planRef=_first_text(getattr(record, "plan_ref", None)),
        manifestRef=_first_text(getattr(record, "manifest_ref", None)),
        artifactRefs=artifact_refs,
        commits=commits,
        pullRequestUrl=pull_request_url,
    )
    return RunDigest(
        namespaceId=namespace,
        repo=repo,
        securityScope=f"repo:{repo}" if repo else f"namespace:{namespace}",
        workflowType=workflow_type,
        workflowId=workflow_id,
        runId=run_id,
        state=state or "unknown",
        closeStatus=close_status,
        intent=_compact(title),
        outcome=outcome,
        keyChanges=_key_changes(
            state=state,
            close_status=close_status,
            artifact_refs=artifact_refs,
            pull_request_url=pull_request_url,
        ),
        keyDecisions=_key_decisions(params=params, memo=memo),
        gotchas=_gotchas(state=state, memo=memo),
        nextSteps=_next_steps(
            state=state,
            close_status=close_status,
            pull_request_url=pull_request_url,
            artifact_refs=artifact_refs,
        ),
        evidence=evidence,
    )


def run_digest_artifact_payload(digest: RunDigest) -> dict[str, Any]:
    """Return the bounded artifact envelope persisted for a run digest.

    The envelope carries the digest JSON plus stable identity/provenance links
    (workflow/run identity, commits, source refs, checkpoint-linked artifact
    refs). It performs no embedding and references no vector collection.
    """

    return {
        "schemaVersion": RUN_DIGEST_ARTIFACT_SCHEMA_VERSION,
        "recordKind": RUN_DIGEST_RECORD_KIND,
        "source": f"run_digest:{digest.workflow_id}",
        "trustClass": RUN_DIGEST_TRUST_CLASS,
        "digest": digest.model_dump(mode="json", by_alias=True),
    }


def _key_changes(
    *,
    state: str,
    close_status: str | None,
    artifact_refs: tuple[str, ...],
    pull_request_url: str | None,
) -> tuple[str, ...]:
    changes: list[str] = []
    if pull_request_url:
        changes.append(f"Published pull request: {pull_request_url}")
    if artifact_refs:
        changes.append(f"Produced {len(artifact_refs)} evidence artifact(s).")
    if not changes and state == "completed":
        changes.append("Completed without recorded publish metadata.")
    elif not changes:
        changes.append(f"Terminal status: {close_status or state or 'unknown'}.")
    return tuple(changes[:_MAX_LIST_ITEMS])


def _key_decisions(
    *,
    params: Mapping[str, Any],
    memo: Mapping[str, Any],
) -> tuple[str, ...]:
    decisions: list[str] = []
    publish_mode = _first_text(params.get("publishMode"), params.get("publish_mode"))
    if publish_mode:
        decisions.append(f"Publish mode: {publish_mode}.")
    if _first_text(memo.get("continue_as_new_cause")):
        decisions.append(
            f"Continue-as-new cause: {_first_text(memo.get('continue_as_new_cause'))}."
        )
    return tuple(decisions[:_MAX_LIST_ITEMS])


def _gotchas(*, state: str, memo: Mapping[str, Any]) -> tuple[str, ...]:
    gotchas: list[str] = []
    error_category = _first_text(memo.get("error_category"))
    if error_category:
        gotchas.append(f"Failure category: {error_category}.")
    if state in {"failed", "canceled"} and not gotchas:
        gotchas.append(f"Run ended as {state}.")
    return tuple(gotchas[:_MAX_LIST_ITEMS])


def _next_steps(
    *,
    state: str,
    close_status: str | None,
    pull_request_url: str | None,
    artifact_refs: tuple[str, ...],
) -> tuple[str, ...]:
    if state == "failed":
        return ("Review diagnostics and rerun after addressing the failure.",)
    if state == "canceled":
        return ("Review cancellation reason before retrying.",)
    if pull_request_url:
        return ("Review and merge the published pull request when appropriate.",)
    if artifact_refs:
        return ("Review produced artifacts for follow-up work.",)
    return (f"No follow-up recorded for terminal status {close_status or state}.",)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _enum_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def _required_text(value: Any, name: str) -> str:
    text = _first_text(value)
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return _compact(text)
    return ""


def _compact(value: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= _MAX_TEXT:
        return text
    return text[: _MAX_TEXT - 3].rstrip() + "..."


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = list(value)
    else:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _first_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
        if len(normalized) >= _MAX_LIST_ITEMS:
            break
    return tuple(normalized)
