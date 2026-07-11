"""Pure canonical resolver models; this module deliberately has no host imports."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class ResolverAction(str, Enum):
    WAIT = "wait"
    RUN_REMEDIATION = "run_remediation"
    ATTEMPT_MERGE = "attempt_merge"
    PUBLISH_TERMINAL = "publish_terminal"
    STOP_MANUAL_REVIEW = "stop_manual_review"


@dataclass(frozen=True, slots=True)
class ResolverSnapshot:
    merged: bool = False
    closed: bool = False
    draft: bool = False
    mergeable: bool | None = None
    checks: str = "unknown"
    comments: str = "unknown"
    publish_available: bool = True
    head_sha: str = ""
    base_sha: str = ""


@dataclass(frozen=True, slots=True)
class ResolverState:
    remediation_attempts: int = 0
    max_remediation_attempts: int = 5
    previous_head_sha: str = ""
    terminal_published: bool = False


@dataclass(frozen=True, slots=True)
class ResolverDecision:
    action: ResolverAction
    reason_code: str
    remediation: str | None = None
    merge_eligible: bool = False


@dataclass(frozen=True, slots=True)
class TerminalResult:
    status: str
    reason_code: str
    verified_head_sha: str = ""
    extensions: Mapping[str, str] = field(default_factory=dict)
