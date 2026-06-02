"""Memory contracts, services, and procedural primitives for MoonMind."""

from moonmind.memory.context_pack import (
    MemoryContextBudgetExceeded,
    MemoryContextCandidate,
    MemoryContextItem,
    MemoryContextPack,
    MemoryPlane,
    build_memory_context_pack,
)
from moonmind.memory.models import (
    ContextPack,
    ContextPackBudget,
    ErrorSignature as MemoryErrorSignature,
    FixPattern as MemoryFixPattern,
    LongTermMemory,
    MemoryCandidate,
    MemoryProvenance,
    RunDigest,
    RunRef,
)
from moonmind.memory.services import (
    InMemoryLongTermMemoryService,
    InMemoryPlanningAdapter,
    InMemoryTaskHistoryStore,
    Mem0LongTermMemoryService,
    RetrievalGateway,
    TaskHistoryService,
)
from moonmind.memory.procedural import (
    EvidenceRun,
    ErrorSignature,
    FileFixPatternStore,
    FixPattern,
    extract_error_signature,
    fix_patterns_to_memory_proposals,
)

__all__ = [
    "EvidenceRun",
    "ErrorSignature",
    "FileFixPatternStore",
    "FixPattern",
    "MemoryContextBudgetExceeded",
    "MemoryContextCandidate",
    "MemoryContextItem",
    "MemoryContextPack",
    "MemoryPlane",
    "build_memory_context_pack",
    "ContextPack",
    "ContextPackBudget",
    "extract_error_signature",
    "fix_patterns_to_memory_proposals",
    "InMemoryLongTermMemoryService",
    "InMemoryPlanningAdapter",
    "InMemoryTaskHistoryStore",
    "LongTermMemory",
    "Mem0LongTermMemoryService",
    "MemoryCandidate",
    "MemoryErrorSignature",
    "MemoryFixPattern",
    "MemoryProvenance",
    "RetrievalGateway",
    "RunDigest",
    "RunRef",
    "TaskHistoryService",
]
