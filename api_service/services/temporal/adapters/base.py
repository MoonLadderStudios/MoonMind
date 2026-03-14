from abc import ABC, abstractmethod
from ..workflows.shared import AgentExecutionRequest, AgentRunHandle, AgentRunStatus, AgentRunResult

class AgentAdapter(ABC):
    @abstractmethod
    def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        pass
        
    @abstractmethod
    def status(self, run_id: str) -> AgentRunStatus:
        pass
        
    @abstractmethod
    def fetch_result(self, run_id: str) -> AgentRunResult:
        pass
        
    @abstractmethod
    def cancel(self, run_id: str) -> None:
        pass
