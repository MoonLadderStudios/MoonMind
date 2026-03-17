"""Adapters bridging MoonMind workflows with external services."""

from .agent_adapter import AgentAdapter
from .codex_client import (
    CodexClient,
    CodexDiffNotReadyError,
    CodexDiffResult,
    CodexDiffRetrievalError,
    CodexSubmissionResult,
)
from .codex_cloud_agent_adapter import CodexCloudAgentAdapter
from .external_adapter_registry import ExternalAdapterRegistry, build_default_registry
from .github_client import GitHubClient, GitHubPublishResult
from .jules_agent_adapter import JulesAgentAdapter

__all__ = [
    "AgentAdapter",
    "CodexClient",
    "CodexCloudAgentAdapter",
    "CodexDiffNotReadyError",
    "CodexDiffRetrievalError",
    "CodexSubmissionResult",
    "CodexDiffResult",
    "ExternalAdapterRegistry",
    "GitHubClient",
    "GitHubPublishResult",
    "JulesAgentAdapter",
    "build_default_registry",
]
