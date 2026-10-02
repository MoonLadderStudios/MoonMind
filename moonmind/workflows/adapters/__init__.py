"""Adapters bridging MoonMind workflows with external services."""

from .agent_adapter import AgentAdapter
from .base_external_agent_adapter import BaseExternalAgentAdapter
from .external_adapter_registry import ExternalAdapterRegistry, build_default_registry
from .github_client import GitHubClient, GitHubPublishResult
from .jules_agent_adapter import JulesAgentAdapter
from .omnigent_agent_adapter import OmnigentExternalAdapter

__all__ = [
    "AgentAdapter",
    "BaseExternalAgentAdapter",
    "ExternalAdapterRegistry",
    "GitHubClient",
    "GitHubPublishResult",
    "JulesAgentAdapter",
    "OmnigentExternalAdapter",
    "build_default_registry",
]
