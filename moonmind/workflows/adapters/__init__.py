"""Adapters bridging MoonMind workflows with external services."""

from .agent_adapter import AgentAdapter
from .base_external_agent_adapter import BaseExternalAgentAdapter
from .external_adapter_registry import ExternalAdapterRegistry, build_default_registry
from .github_client import GitHubClient, GitHubPublishResult
from .omnigent_agent_adapter import OmnigentExternalAdapter

__all__ = [
    "AgentAdapter",
    "BaseExternalAgentAdapter",
    "ExternalAdapterRegistry",
    "GitHubClient",
    "GitHubPublishResult",
    "OmnigentExternalAdapter",
    "build_default_registry",
]
