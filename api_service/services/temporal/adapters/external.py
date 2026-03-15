import uuid
import datetime
from typing import Dict, Any

from .base import AgentAdapter
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunHandle, AgentRunStatus, AgentRunResult

class ExternalAgentAdapter(AgentAdapter):
    def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        run_id = request.idempotency_key or f"ext-{uuid.uuid4()}"
        
        # DOC-REQ-EXT-RESP: translate request to provider payload
        payload = self._translate_to_provider_payload(request)
        
        # DOC-REQ-EXT-RESP: exchange artifacts and pass callbacks
        self._provision_artifact_exchange(payload)
        self._register_callbacks(run_id)
        
        return AgentRunHandle(
            runId=run_id,
            agentKind="external",
            agentId=request.agent_id,
            status=AgentRunStatus.launching,
            startedAt=datetime.datetime.utcnow().isoformat() + "Z",
            metadata={"poll_hint_seconds": 15}  # DOC-REQ-POLLING
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
        return AgentRunStatus(
            runId=run_id,
            agentKind="external",
            agentId="stub",
            status="running"
        )
        
    def fetch_result(self, run_id: str) -> AgentRunResult:
        # DOC-REQ-EXT-RESP: fetch outputs/diagnostics
        diagnostics_ref = f"diag-{run_id}"
        return AgentRunResult(
            summary="External run complete",
            outputRefs=[],
            metadata={"diagnostics_ref": diagnostics_ref}
        )
        
    def cancel(self, run_id: str) -> AgentRunStatus:
        # DOC-REQ-EXT-RESP: cancel remote work
        return AgentRunStatus(
            runId=run_id,
            agentKind="external",
            agentId="stub",
            status="canceled"
        )
