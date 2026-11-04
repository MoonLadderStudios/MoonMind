"""Deterministic GitLab merge-request tool: bounded reads plus one admitted note.

``gitlab.merge_request`` runs ``GitLabMRAdapter`` on the trusted
``mm.tool.execute`` path. The step names one recorded repository connection;
the connection service admits only a verified assignment of the project and
its operations, and the existing bound acquisition reads the connection's own
SecretRef. Nothing here discovers ambient tokens or accepts credentials from
inputs.

The note's operation identity is derived from the workflow, the logical step,
and the admitted body, so an Activity retry or a restarted step reuses it and
the adapter's footer reconciliation runs before any repeat POST. Every outcome
is persisted as an ``output.primary`` result artifact; an uncertain delivery
keeps the exact work and its next automated action instead of reporting
success.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Awaitable, Callable, Literal, Mapping

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from moonmind.integrations.gitlab.adapter import GitLabMRAdapter
from moonmind.integrations.gitlab.client import (
    GitLabClient,
    connection_from_bound_credential,
)
from moonmind.integrations.gitlab.errors import GitLabToolError
from moonmind.integrations.gitlab.identity import resolve_gitlab_identity
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure, ToolResult

TOOL_NAME = "gitlab.merge_request"
_RESULT_SCHEMA = "moonmind.gitlab-merge-request-result.v1"
_READS = ("mr", "discussions", "status")
_MAX_NOTE_CHARS = 100_000
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
# Retryable acquisition failures: the credential store or issuer is down.
_RETRYABLE_BOUND_CODES = frozenset({"BOUND_ISSUER_FAILED", "BOUND_UNAVAILABLE"})

ConnectionLoader = Callable[[str, str], Awaitable[Any]]


class GitLabNoteInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    body: str = Field(min_length=1, max_length=_MAX_NOTE_CHARS)
    # Continues an earlier operation's handoff; a footer-safe idempotency key,
    # never authority.
    operation_id: str | None = Field(
        None, alias="operationId", pattern=r"^[A-Za-z0-9:._-]{1,200}$"
    )

    @field_validator("body")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note body is required")
        return value.strip()


class GitLabMergeRequestInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    connection_ref: str = Field(alias="connectionRef", min_length=1)
    endpoint: str | None = None
    project: str = Field(min_length=1)
    mr_iid: int = Field(alias="mrIid", gt=0)
    read: tuple[Literal["mr", "discussions", "status"], ...] = _READS
    note: GitLabNoteInput | None = None


def build_gitlab_merge_request_tool_definition_payload() -> dict[str, Any]:
    """Trusted registry entry; it advertises only the implemented operations."""

    return {
        "name": TOOL_NAME,
        "description": (
            "Read one GitLab merge request with its discussions and status, and "
            "optionally post one ordinary note, through a recorded repository "
            "connection."
        ),
        "inputs": {
            "schema": {
                "type": "object",
                "required": ["connectionRef", "project", "mrIid"],
                "properties": {
                    "connectionRef": {"type": "string"},
                    "endpoint": {"type": "string"},
                    "project": {"type": "string"},
                    "mrIid": {"type": "integer"},
                    "read": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(_READS)},
                    },
                    "note": {
                        "type": "object",
                        "required": ["body"],
                        "properties": {
                            "body": {"type": "string"},
                            "operationId": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            }
        },
        "outputs": {
            "schema": {
                "type": "object",
                "required": ["target", "readsComplete", "note"],
                "properties": {
                    "target": {"type": "object"},
                    "mr": {"type": "object"},
                    "discussions": {"type": "object"},
                    "mrStatus": {"type": "object"},
                    "readsComplete": {"type": "boolean"},
                    "note": {"type": "object"},
                    "resultArtifactRef": {"type": "string"},
                    "error": {"type": "object"},
                    "summary": {"type": "string"},
                },
                "additionalProperties": True,
            }
        },
        "executor": {
            "activity_type": "mm.tool.execute",
            "selector": {"mode": "by_capability"},
        },
        "requirements": {"capabilities": ["integration:gitlab"]},
        "policies": {
            "timeouts": {
                "start_to_close_seconds": 300,
                "schedule_to_close_seconds": 900,
            },
            # A retried attempt reconciles the note footer before any POST.
            "retries": {"max_attempts": 3},
        },
    }


def note_operation_id(*, workflow_id: str, logical_step_id: str, body: str) -> str:
    """Stable across Activity retries and step restarts; new for a new body."""

    body_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    digest = hashlib.sha256(
        f"{workflow_id}\n{logical_step_id}\n{body_digest}".encode("utf-8")
    ).hexdigest()
    return f"mm-note-{digest[:32]}"


async def _load_recorded_connection(connection_ref: str, repository: str) -> Any:
    """The recorded connection narrowed to the project's verified assignment."""

    from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
        load_repository_connection_for_launch,
    )

    return await load_repository_connection_for_launch(
        connection_ref, repository=repository
    )


class _Outcome(Exception):
    """A terminal, already classified result of this attempt."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _error_of(exc: Exception) -> _Outcome:
    code = str(getattr(exc, "code", "") or type(exc).__name__)
    if isinstance(exc, GitLabToolError):
        retryable = code == "gitlab_transient" or exc.status_code in _RETRYABLE_STATUSES
        return _Outcome(code, exc.args[0], retryable=retryable)
    return _Outcome(code, str(exc), retryable=code in _RETRYABLE_BOUND_CODES)


class GitLabMergeRequestTool:
    """Handler for ``gitlab.merge_request`` with injectable boundary seams."""

    def __init__(
        self,
        *,
        load_connection: ConnectionLoader | None = None,
        resolve_secret: Callable[[str], Any] | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._load_connection = load_connection or _load_recorded_connection
        # ``None`` selects the Secrets-System resolver of bound acquisition.
        self._resolve_secret = resolve_secret
        self._transport = http_transport

    async def __call__(
        self, inputs: Mapping[str, Any], context: Mapping[str, Any] | None = None
    ) -> ToolResult:
        try:
            request = GitLabMergeRequestInput.model_validate(inputs)
        except ValidationError as exc:
            raise ToolFailure(
                "INVALID_INPUT", "Invalid GitLab merge-request request.", False
            ) from exc
        context = dict(context or {})
        required = ("namespace", "workflow_id", "run_id", "node_id")
        if any(not context.get(key) for key in required) or not context.get(
            "temporal_artifact_service"
        ):
            raise ToolFailure(
                "INVALID_INPUT",
                "GitLab merge-request tool requires its execution and artifact context.",
                False,
            )
        attempt = _Attempt(self, request, context)
        return await attempt.run()


class _Attempt:
    """One activity attempt: admit, acquire, read, note, persist."""

    def __init__(
        self,
        tool: GitLabMergeRequestTool,
        request: GitLabMergeRequestInput,
        context: Mapping[str, Any],
    ) -> None:
        self.tool = tool
        self.request = request
        self.context = context
        self.execution = {
            "namespace": context["namespace"],
            "workflow_id": context["workflow_id"],
            "run_id": context["run_id"],
        }
        self.target = {
            "connectionRef": request.connection_ref,
            "endpoint": request.endpoint,
            "project": request.project,
            "mrIid": request.mr_iid,
        }
        self.reads: dict[str, Any] = {}
        note = request.note
        self.note: dict[str, Any] = {"deliveryState": "not_requested"}
        if note is not None:
            operation_id = note.operation_id or note_operation_id(
                workflow_id=context["workflow_id"],
                logical_step_id=context["node_id"],
                body=note.body,
            )
            self.note = {
                "operationId": operation_id,
                "bodySha256": hashlib.sha256(note.body.encode("utf-8")).hexdigest(),
                "deliveryState": "not_attempted",
            }

    async def run(self) -> ToolResult:
        try:
            adapter, clients = await self._open_adapter()
        except _Outcome as outcome:
            return await self._finish("failed", error=outcome)
        error: _Outcome | None = None
        try:
            await self._read(adapter)
            if self.request.note is not None:
                await self._post_note(adapter)
        except _Outcome as outcome:
            error = outcome
        finally:
            for client in clients:
                await client.aclose()
        if self.note["deliveryState"] == "uncertain":
            return await self._finish("uncertain", error=error)
        return await self._finish("failed" if error else "completed", error=error)

    async def _open_adapter(self) -> tuple[GitLabMRAdapter, list[Any]]:
        """Admit endpoint, project and operations before touching a secret."""

        from moonmind.auth.bound_acquisition import BoundAccessError
        from moonmind.auth.github_app_wiring import (
            acquire_bound_credential_for_connection,
        )
        from moonmind.workflows.executions.repository_contract import (
            RepositoryContractError,
            RepositoryRouteError,
        )
        from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
            _LAUNCH_CONNECTION_PRINCIPAL,
        )

        request = self.request
        action = "post_note" if request.note is not None else "read_mr"
        try:
            connection = await self.tool._load_connection(
                request.connection_ref, request.project
            )
            if connection is None:
                raise _Outcome(
                    "REPOSITORY_CONNECTION_UNAVAILABLE",
                    f"repository connection {request.connection_ref!r} is not "
                    "recorded; no other connection is substituted",
                )
            if (
                connection.provider != "git"
                or connection.hosting_service != "generic_git"
                or connection.credential.source != "secret_ref"
            ):
                raise _Outcome(
                    "gitlab_connection_unsupported",
                    "GitLab merge-request access requires a generic Git "
                    "connection with a Secret-backed token.",
                )
            admitted = (connection.endpoint_ref,)
            identity = resolve_gitlab_identity(
                endpoint=request.endpoint or connection.endpoint_ref,
                project=request.project,
                mr_iid=request.mr_iid,
                admitted_endpoints=admitted,
                action=action,
            )
            self.target["endpoint"] = identity.project.endpoint
            acquired = await acquire_bound_credential_for_connection(
                connection,
                operations=(
                    ("read", "review_request")
                    if request.note is not None
                    else ("read",)
                ),
                principal_ref=_LAUNCH_CONNECTION_PRINCIPAL,
                execution_owner=self.execution["workflow_id"],
                operation_id=f"{TOOL_NAME}:{self.execution['workflow_id']}:"
                f"{self.context['node_id']}",
                repository_display=request.project,
                role="collaboration",
                resolve_secret=self.tool._resolve_secret,
            )
            try:
                resolved = connection_from_bound_credential(
                    acquired,
                    endpoint=identity.project.endpoint,
                    admitted_endpoints=admitted,
                    action=action,
                )
            finally:
                acquired.credential.clear()
        except (
            BoundAccessError,
            GitLabToolError,
            RepositoryContractError,
            RepositoryRouteError,
        ) as exc:
            raise _error_of(exc) from None
        http = None
        if self.tool._transport is not None:
            http = httpx.AsyncClient(
                base_url=resolved.base_url,
                headers=resolved.headers,
                transport=self.tool._transport,
                follow_redirects=False,
            )
        client = GitLabClient(connection=resolved, client=http)
        clients = [client] if http is None else [client, http]
        return GitLabMRAdapter(client=client, identity=identity), clients

    async def _read(self, adapter: GitLabMRAdapter) -> None:
        readers = {
            "mr": adapter.read_mr,
            "discussions": adapter.read_discussions,
            "status": adapter.read_status,
        }
        for name in dict.fromkeys(self.request.read):
            try:
                self.reads[name] = await readers[name]()
            except GitLabToolError as exc:
                raise _error_of(exc) from None

    async def _post_note(self, adapter: GitLabMRAdapter) -> None:
        note = self.request.note
        assert note is not None
        try:
            result = await adapter.post_note(
                body=note.body, operation_id=self.note["operationId"]
            )
        except GitLabToolError as exc:
            outcome = _error_of(exc)
            if outcome.retryable:
                # The POST may have landed: the retry reconciles first.
                self.note.update(
                    deliveryState="uncertain", reason="acknowledgment_lost"
                )
            else:
                # Refused before or by the POST: this attempt created no note.
                self.note.update(deliveryState="not_delivered")
            raise outcome from None
        handoff = result.get("automation_handoff")
        if handoff is not None:
            self.note.update(
                deliveryState="uncertain",
                reason=handoff["reason"],
                automationHandoff={**handoff, "nextAction": self._next_action()},
            )
            return
        self.note.update(
            deliveryState="reconciled" if result.get("reconciled") else "delivered",
            noteId=result.get("note_id"),
        )

    def _next_action(self) -> dict[str, Any]:
        """The same admitted operation, runnable by automation."""

        return {
            "tool": TOOL_NAME,
            "inputs": {
                "connectionRef": self.request.connection_ref,
                "project": self.request.project,
                "mrIid": self.request.mr_iid,
                "read": [],
                "note": {"operationId": self.note["operationId"]},
            },
            "noteBody": "the result artifact's note.body",
            "effect": "reconciles the operation footer again before any POST",
        }

    def _reads_complete(self) -> bool:
        discussions = self.reads.get("discussions")
        status = self.reads.get("status")
        return (discussions is None or discussions["completeness"]["complete"]) and (
            status is None or status["completeness"]["complete"]
        )

    def _compact_reads(self) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        if (mr := self.reads.get("mr")) is not None:
            outputs["mr"] = {
                "iid": mr["iid"],
                "title": mr["title"],
                "state": mr["state"],
                "sourceBranch": mr["source_branch"],
                "targetBranch": mr["target_branch"],
                "headSha": mr["head_sha"],
                "webUrl": mr["web_url"],
            }
        if (discussions := self.reads.get("discussions")) is not None:
            outputs["discussions"] = {
                "count": len(discussions["discussions"]),
                "complete": discussions["completeness"]["complete"],
            }
        if (status := self.reads.get("status")) is not None:
            outputs["mrStatus"] = {
                "state": status["state"],
                "headSha": status["head_sha"],
                "mergeableKnown": status["mergeable_known"],
                "blockers": status["blockers"],
                "blockingUnknown": status["blocking_unknown"],
                "pipelineCount": len(status["pipelines"]),
                "complete": status["completeness"]["complete"],
            }
        return outputs

    async def _finish(
        self, outcome: str, *, error: _Outcome | None = None
    ) -> ToolResult:
        if error is not None and error.retryable and outcome != "uncertain":
            # Nothing was written: Temporal retries the whole attempt.
            raise ToolFailure("EXTERNAL_FAILED", error.message, True)
        error_payload = (
            None if error is None else {"code": error.code, "message": error.message}
        )
        record = {
            "schemaVersion": _RESULT_SCHEMA,
            "tool": TOOL_NAME,
            "outcome": outcome,
            "target": self.target,
            "execution": {
                "workflowId": self.execution["workflow_id"],
                "runId": self.execution["run_id"],
                "logicalStepId": self.context["node_id"],
            },
            "reads": self.reads,
            "readsComplete": self._reads_complete(),
            # The admitted body stays with its operation so an uncertain
            # delivery can be continued exactly.
            "note": (
                {**self.note, "body": self.request.note.body}
                if self.request.note is not None
                else self.note
            ),
            "error": error_payload,
        }
        outputs: dict[str, Any] = {
            "target": self.target,
            **self._compact_reads(),
            "readsComplete": record["readsComplete"],
            "note": self.note,
            "summary": self._summary(outcome, error),
        }
        if error_payload is not None:
            outputs["error"] = error_payload
        try:
            outputs["resultArtifactRef"] = await self._persist(record)
        except Exception as exc:
            # Confirmed GitLab effects survive a failed result write.
            outputs["diagnostics"] = [
                {"boundary": "result-artifact", "error": type(exc).__name__}
            ]
        if outcome == "uncertain" and error is not None and error.retryable:
            raise ToolFailure(
                "EXTERNAL_FAILED",
                f"GitLab note {self.note['operationId']} acknowledgment was lost; "
                "the retry reconciles its footer before posting.",
                True,
                details={
                    "operationId": self.note["operationId"],
                    "resultArtifactRef": outputs.get("resultArtifactRef"),
                },
            )
        return ToolResult(
            status="COMPLETED" if outcome == "completed" else "FAILED",
            outputs=outputs,
        )

    async def _persist(self, record: dict[str, Any]) -> str:
        service = self.context["temporal_artifact_service"]
        artifact, _ = await service.put_content_addressed_payload_complete(
            principal=str(
                self.context.get("deployment_evidence_principal")
                or "system:gitlab-merge-request"
            ),
            payload=json.dumps(record, sort_keys=True, separators=(",", ":")).encode(),
            content_type="application/json",
            scope="gitlab-merge-requests",
            link={**self.execution, "link_type": "output.primary"},
            metadata_json={
                "artifact_type": "gitlab_merge_request_result",
                "producer": TOOL_NAME,
                "subject": self.note.get("operationId") or self.context["node_id"],
                "step_id": self.context["node_id"],
            },
        )
        return artifact.artifact_id

    def _summary(self, outcome: str, error: _Outcome | None) -> str:
        target = f"{self.target['project']}!{self.target['mrIid']}"
        if error is not None and outcome == "failed":
            return f"GitLab {target}: {error.code}."
        state = self.note["deliveryState"]
        reads = ", ".join(self.reads) or "no reads"
        if state == "uncertain":
            return (
                f"GitLab {target}: read {reads}; note delivery is uncertain "
                f"({self.note['reason']}); the operation is retained for retry."
            )
        complete = "complete" if self._reads_complete() else "incomplete"
        return f"GitLab {target}: read {reads} ({complete}); note {state}."


def register_gitlab_merge_request_tool_handler(
    dispatcher: Any, *, tool: GitLabMergeRequestTool | None = None
) -> None:
    dispatcher.register_skill(
        skill_name=TOOL_NAME, handler=tool or GitLabMergeRequestTool()
    )
