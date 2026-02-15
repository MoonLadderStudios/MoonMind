"""Skills-first workflow execution helpers.

This package provides a lightweight policy and execution adapter that lets
workflow stages resolve a selected skill while preserving compatibility with the
existing direct execution path.
"""

from .contracts import StageExecutionDecision, StageExecutionOutcome
from .runner import execute_stage

__all__ = [
    "StageExecutionDecision",
    "StageExecutionOutcome",
    "execute_stage",
]
