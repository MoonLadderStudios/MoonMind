from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

AGENT_SESSION_WORKFLOW_PATH = "moonmind/workflows/temporal/workflows/agent_session.py"
AGENT_SESSION_REPLAYER_TEST_PATH = (
    "tests/unit/workflows/temporal/test_agent_session_replayer.py"
)
AGENT_SESSION_CUTOVER_PLAYBOOK_PATH = (
    "docs/ManagedAgents/AgentSessionDeploymentSafetyCutover.md"
)

AGENT_SESSION_DEPLOYMENT_SENSITIVE_PATHS = frozenset(
    {
        AGENT_SESSION_WORKFLOW_PATH,
        "moonmind/schemas/managed_session_models.py",
        "moonmind/workflows/temporal/workflows/run.py",
        "moonmind/workflows/temporal/client.py",
        "moonmind/workflows/temporal/worker_runtime.py",
    }
)

REQUIRED_AGENT_SESSION_CUTOVER_TOPICS = frozenset(
    {
        "SteerTurn",
        "Continue-As-New",
        "CancelSession",
        "TerminateSession",
        "Search Attributes",
        "replay-safe rollout",
        "replay",
    }
)

class AgentSessionDeploymentSafetyError(RuntimeError):
    """Raised when a deployment-sensitive AgentSession change lacks rollout gates."""

@dataclass(frozen=True)
class AgentSessionDeploymentSafetyReport:
    required: bool
    changed_sensitive_paths: tuple[str, ...]
    replay_gate_path: str
    cutover_playbook_path: str
    active_feature_dir: str | None = None

def normalize_repo_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")

def changed_agent_session_sensitive_paths(paths: Iterable[str | Path]) -> tuple[str, ...]:
    changed_paths = {normalize_repo_path(path) for path in paths if str(path).strip()}
    sensitive = sorted(
        path
        for path in changed_paths
        if path in AGENT_SESSION_DEPLOYMENT_SENSITIVE_PATHS
        or path.startswith("moonmind/workflows/temporal/workflows/agent_session")
    )
    return tuple(sensitive)

def resolve_active_feature_dir(
    *,
    repo_root: str | Path,
    active_feature: str | Path | None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a Spec Kit feature override to a validated repo-relative path."""

    raw_value = str(active_feature or "").strip()
    if not raw_value and env is not None:
        raw_value = str(env.get("SPECIFY_FEATURE") or "").strip()
    if not raw_value:
        return None

    if Path(raw_value).is_absolute():
        raise AgentSessionDeploymentSafetyError(
            f"active feature override must stay within specs/: {raw_value}"
        )

    normalized = normalize_repo_path(raw_value)
    normalized_path = Path(normalized)
    if ".." in normalized_path.parts:
        raise AgentSessionDeploymentSafetyError(
            f"active feature override must stay within specs/: {normalized}"
        )
    relative = normalized if normalized.startswith("specs/") else f"specs/{normalized}"
    root = Path(repo_root)
    specs_root = (root / "specs").resolve()
    feature_dir = (root / relative).resolve()
    try:
        feature_dir.relative_to(specs_root)
    except ValueError as exc:
        raise AgentSessionDeploymentSafetyError(
            f"active feature override must stay within specs/: {relative}"
        ) from exc
    if not feature_dir.is_dir():
        raise AgentSessionDeploymentSafetyError(
            f"active feature override does not exist: {relative}"
        )

    required_artifacts = ("spec.md", "plan.md", "tasks.md")
    missing = [
        artifact for artifact in required_artifacts if not (feature_dir / artifact).is_file()
    ]
    if missing:
        raise AgentSessionDeploymentSafetyError(
            "active feature override is missing artifacts: " + ", ".join(missing)
        )
    return relative

def validate_agent_session_deployment_safety(
    *,
    changed_paths: Iterable[str | Path],
    repo_paths: Iterable[str | Path],
    cutover_playbook_text: str,
    active_feature_dir: str | Path | None = None,
) -> AgentSessionDeploymentSafetyReport:
    changed_sensitive_paths = changed_agent_session_sensitive_paths(changed_paths)
    repo_path_set = {normalize_repo_path(path) for path in repo_paths if str(path).strip()}
    missing: list[str] = []

    if changed_sensitive_paths:
        if AGENT_SESSION_REPLAYER_TEST_PATH not in repo_path_set:
            missing.append(
                f"missing managed-session replay gate: {AGENT_SESSION_REPLAYER_TEST_PATH}"
            )
        if AGENT_SESSION_CUTOVER_PLAYBOOK_PATH not in repo_path_set:
            missing.append(
                f"missing cutover playbook: {AGENT_SESSION_CUTOVER_PLAYBOOK_PATH}"
            )
        missing_topics = sorted(
            topic
            for topic in REQUIRED_AGENT_SESSION_CUTOVER_TOPICS
            if topic not in cutover_playbook_text
        )
        if missing_topics:
            missing.append(
                "cutover playbook is missing required topics: "
                + ", ".join(missing_topics)
            )

    if missing:
        changed = ", ".join(changed_sensitive_paths)
        raise AgentSessionDeploymentSafetyError(
            "AgentSession deployment safety gate failed for "
            f"{changed}: {'; '.join(missing)}"
        )

    return AgentSessionDeploymentSafetyReport(
        required=bool(changed_sensitive_paths),
        changed_sensitive_paths=changed_sensitive_paths,
        replay_gate_path=AGENT_SESSION_REPLAYER_TEST_PATH,
        cutover_playbook_path=AGENT_SESSION_CUTOVER_PLAYBOOK_PATH,
        active_feature_dir=(
            normalize_repo_path(active_feature_dir) if active_feature_dir else None
        ),
    )
