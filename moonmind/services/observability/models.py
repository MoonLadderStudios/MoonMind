from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field

class LogStreamType(str, Enum):
    stdout = "stdout"
    stderr = "stderr"
    system = "system"

class LogStreamEvent(BaseModel):
    sequence: int = Field(..., description="Monotonically increasing sequence ID")
    stream: LogStreamType = Field(..., description="Log source: stdout, stderr, or system")
    offset: int = Field(..., description="Cumulative byte or line offset marker")
    timestamp: datetime = Field(..., description="Exact time the event was produced")
    text: str = Field(..., description="Raw log data payload")
