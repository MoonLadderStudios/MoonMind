"""Plane B task-history run digest models and indexing service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping, Protocol, Sequence
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

RUN_DIGEST_SCHEMA_VERSION = "v1"
RUN_DIGEST_RECORD_KIND = "run_digest"
RUN_DIGEST_TRUST_CLASS = "derived"
_MAX_TEXT = 500
_MAX_LIST_ITEMS = 8


class RunDigestEvidence(BaseModel):
    """Evidence pointers retained with a derived run digest."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    task_run_id: str | None = Field(None, alias="taskRunId")
    summary_artifact_ref: str | None = Field(None, alias="summaryArtifactRef")
    input_ref: str | None = Field(None, alias="inputRef")
    plan_ref: str | None = Field(None, alias="planRef")
    manifest_ref: str | None = Field(None, alias="manifestRef")
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple, alias="artifactRefs")
    commits: tuple[str, ...] = Field(default_factory=tuple, alias="commits")
    pull_request_url: str | None = Field(None, alias="pullRequestUrl")


class RunDigest(BaseModel):
    """Short structured summary of one terminal task run."""

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
    created_at: str = Field(default_factory=lambda: _utc_now(), alias="createdAt")

    def to_context_text(self) -> str:
        """Render a compact, retrievable text body for vector search."""

        sections = [
            ("Intent", self.intent),
            ("Outcome", self.outcome),
            ("Key changes", _join_or_none(self.key_changes)),
            ("Key decisions", _join_or_none(self.key_decisions)),
            ("Gotchas", _join_or_none(self.gotchas)),
            ("Next steps", _join_or_none(self.next_steps)),
        ]
        lines = [
            f"Run Digest for {self.workflow_type} {self.workflow_id}",
            f"State: {self.state}",
        ]
        if self.close_status:
            lines.append(f"Close status: {self.close_status}")
        if self.repo:
            lines.append(f"Repository: {self.repo}")
        for label, value in sections:
            if value:
                lines.append(f"{label}: {value}")
        evidence_bits = [
            f"workflowId={self.evidence.workflow_id}",
            f"runId={self.evidence.run_id}",
        ]
        if self.evidence.task_run_id:
            evidence_bits.append(f"taskRunId={self.evidence.task_run_id}")
        if self.evidence.summary_artifact_ref:
            evidence_bits.append(f"summaryRef={self.evidence.summary_artifact_ref}")
        if self.evidence.artifact_refs:
            evidence_bits.append(
                "artifactRefs=" + ", ".join(self.evidence.artifact_refs[:_MAX_LIST_ITEMS])
            )
        lines.append("Evidence: " + "; ".join(evidence_bits))
        return "\n".join(lines)


class VectorIndex(Protocol):
    """Small protocol for the vector index boundary used by run digests."""

    collection: str

    def upsert_memory_vectors(
        self,
        *,
        vectors: list[list[float]],
        payloads: list[MutableMapping[str, Any]],
        ids: list[str],
        collection_name: str | None = None,
    ) -> None: ...


class EmbeddingProvider(Protocol):
    """Embedding provider protocol used by run-digest indexing."""

    def embed(self, text: str) -> Sequence[float]: ...


class TaskHistoryService:
    """Build and index Plane B run digests from terminal execution records."""

    def __init__(
        self,
        *,
        qdrant_client: VectorIndex,
        embedding_provider: EmbeddingProvider,
        collection_name: str | None = None,
    ) -> None:
        self._qdrant = qdrant_client
        self._embedding_provider = embedding_provider
        self._collection_name = collection_name

    def build_run_digest(self, record: Any) -> RunDigest:
        """Build a compact digest from a terminal execution record."""

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
            git.get("repository"),
            task.get("repository"),
            task.get("repo"),
            params.get("repository"),
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
        task_run_id = _first_text(
            memo.get("taskRunId"),
            memo.get("task_run_id"),
            attrs.get("mm_task_run_id"),
            params.get("taskRunId"),
            params.get("task_run_id"),
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
            taskRunId=task_run_id,
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
            keyChanges=self._key_changes(
                state=state,
                close_status=close_status,
                artifact_refs=artifact_refs,
                pull_request_url=pull_request_url,
            ),
            keyDecisions=self._key_decisions(params=params, memo=memo),
            gotchas=self._gotchas(state=state, memo=memo),
            nextSteps=self._next_steps(
                state=state,
                close_status=close_status,
                pull_request_url=pull_request_url,
                artifact_refs=artifact_refs,
            ),
            evidence=evidence,
        )

    def payload_for_digest(self, digest: RunDigest) -> MutableMapping[str, Any]:
        """Return the Qdrant payload for a run digest."""

        payload = digest.model_dump(mode="json", by_alias=True)
        payload.update(
            {
                "record_kind": RUN_DIGEST_RECORD_KIND,
                "source": f"run_digest:{digest.workflow_id}",
                "text": digest.to_context_text(),
                "trust_class": RUN_DIGEST_TRUST_CLASS,
                "namespace_id": digest.namespace_id,
                "repo": digest.repo,
                "security_scope": digest.security_scope,
                "run_ref.kind": "workflow",
                "run_ref.id": digest.workflow_id,
                "workflowId": digest.workflow_id,
                "runId": digest.run_id,
                "workflow_id": digest.workflow_id,
                "run_id": digest.run_id,
            }
        )
        if digest.evidence.task_run_id:
            payload["taskRunId"] = digest.evidence.task_run_id
            payload["task_run_id"] = digest.evidence.task_run_id
        return payload

    def upsert_run_digest(self, digest: RunDigest) -> MutableMapping[str, Any]:
        """Embed and upsert a run digest into the configured vector collection."""

        payload = self.payload_for_digest(digest)
        vector = list(self._embedding_provider.embed(payload["text"]))
        point_id = str(uuid5(NAMESPACE_URL, f"moonmind:run_digest:{digest.workflow_id}"))
        self._qdrant.upsert_memory_vectors(
            vectors=[vector],
            payloads=[payload],
            ids=[point_id],
            collection_name=self._collection_name,
        )
        return {
            "recordKind": RUN_DIGEST_RECORD_KIND,
            "workflowId": digest.workflow_id,
            "runId": digest.run_id,
            "source": payload["source"],
            "collection": self._collection_name or self._qdrant.collection,
        }

    def build_and_upsert_run_digest(self, record: Any) -> MutableMapping[str, Any]:
        digest = self.build_run_digest(record)
        return self.upsert_run_digest(digest)

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def _gotchas(*, state: str, memo: Mapping[str, Any]) -> tuple[str, ...]:
        gotchas: list[str] = []
        error_category = _first_text(memo.get("error_category"))
        if error_category:
            gotchas.append(f"Failure category: {error_category}.")
        if state in {"failed", "canceled"} and not gotchas:
            gotchas.append(f"Run ended as {state}.")
        return tuple(gotchas[:_MAX_LIST_ITEMS])

    @staticmethod
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


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


def _join_or_none(items: Sequence[str]) -> str:
    return "; ".join(item for item in items if item)
