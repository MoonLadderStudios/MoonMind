from abc import ABC, abstractmethod
from ..workflows.shared import AgentExecutionRequest, AgentRunHandle, AgentRunStatus, AgentRunResult

class AgentAdapter(ABC):
    @abstractmethod
    async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        pass

    @abstractmethod
    async def status(self, run_id: str) -> AgentRunStatus:
        pass

    @abstractmethod
    async def fetch_result(self, run_id: str) -> AgentRunResult:
        pass

    @abstractmethod
    async def cancel(self, run_id: str) -> None:
        pass
