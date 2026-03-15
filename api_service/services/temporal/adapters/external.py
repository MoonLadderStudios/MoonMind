import uuid
import datetime
from typing import Dict, Any
from .base import AgentAdapter
from ..workflows.shared import AgentExecutionRequest, AgentRunHandle, AgentRunStatus, AgentRunResult

class ExternalAgentAdapter(AgentAdapter):
    async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        run_id = request.idempotency_key or f"ext-{uuid.uuid4()}"

        # DOC-REQ-EXT-RESP: translate request to provider payload
        payload = self._translate_to_provider_payload(request)

        # DOC-REQ-EXT-RESP: exchange artifacts and pass callbacks
        self._provision_artifact_exchange(payload)
        self._register_callbacks(run_id)

        return AgentRunHandle(
            run_id=run_id,
            agent_kind="external",
            agent_id=request.agent_id,
            status=AgentRunStatus.launching,
            started_at=datetime.datetime.utcnow().isoformat() + "Z",
            poll_hint_seconds=15  # DOC-REQ-POLLING
        )

    def _translate_to_provider_payload(self, request: AgentExecutionRequest) -> Dict[str, Any]:
        return {
            "agent_id": request.agent_id,
            "instruction": request.instruction_ref,
            "inputs": request.input_refs
        }

    def _provision_artifact_exchange(self, payload: Dict[str, Any]) -> None:
        # Mock presigned URLs or similar
        pass

    def _register_callbacks(self, run_id: str) -> None:
        # Mock passing webhook endpoints
        pass

    def status(self, run_id: str) -> AgentRunStatus:
        # DOC-REQ-POLLING: bounded status polling fallback
        return AgentRunStatus.running

    def fetch_result(self, run_id: str) -> AgentRunResult:
        # DOC-REQ-EXT-RESP: fetch outputs/diagnostics
        diagnostics_ref = f"diag-{run_id}"
        return AgentRunResult(summary="External run complete", output_refs=[], diagnostics_ref=diagnostics_ref)

    async def cancel(self, run_id: str) -> None:
        # DOC-REQ-EXT-RESP: cancel remote work
        pass
