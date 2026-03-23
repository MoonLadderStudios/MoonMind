"""Exclude test modules that reference the deleted agent_queue system."""

collect_ignore_glob = [
    "api/routers/test_task_runs.py",
    "api/routers/test_mcp_tools.py",
    "api/routers/test_agent_queue_artifacts.py",
    "mcp/test_tool_registry.py",
]
