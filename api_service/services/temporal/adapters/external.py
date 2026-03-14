from .base import AgentAdapter
from ..workflows.shared import AgentExecutionRequest, AgentRunHandle, AgentRunStatus, AgentRunResult

class ExternalAgentAdapter(AgentAdapter):
    def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        # Stub implementation
        return AgentRunHandle(
            run_id="external-run-123",
            agent_kind="external",
            agent_id=request.agent_id,
            status=AgentRunStatus.launching,
            started_at="2026-03-14T00:00:00Z"
        )
        
    def status(self, run_id: str) -> AgentRunStatus:
        # Stub implementation
        return AgentRunStatus.running
        
    def fetch_result(self, run_id: str) -> AgentRunResult:
        # Stub implementation
        return AgentRunResult(summary="External run complete")
        
    def cancel(self, run_id: str) -> None:
        pass
