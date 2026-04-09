from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DOOD_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "DockerOutOfDocker.md"
SESSION_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "CodexManagedSessionPlane.md"
EXECUTION_DOC = (
    REPO_ROOT / "docs" / "Temporal" / "ManagedAndExternalAgentExecutionModel.md"
)
TRACKER = (
    REPO_ROOT
    / "docs"
    / "tmp"
    / "remaining-work"
    / "ManagedAgents-DockerOutOfDocker.md"
)
TRACKER_INDEX = REPO_ROOT / "docs" / "tmp" / "remaining-work" / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_canonical_dood_doc_links_phase0_tracker() -> None:
    dood_text = _read(DOOD_DOC)

    assert "ManagedAgents-DockerOutOfDocker.md" in dood_text
    assert TRACKER.exists()


def test_session_plane_doc_declares_session_assisted_workloads_outside_identity() -> None:
    session_text = _read(SESSION_DOC)

    assert "control-plane tools" in session_text
    assert "workload containers remain outside session identity" in session_text


def test_execution_model_doc_keeps_docker_workloads_on_tool_path() -> None:
    execution_text = _read(EXECUTION_DOC)

    assert "Docker-backed workload tools are ordinary executable tools" in execution_text
    assert (
        "not new `MoonMind.AgentRun` instances unless the launched runtime is itself"
        in execution_text
    )


def test_dood_glossary_and_scope_terms_remain_present() -> None:
    dood_text = _read(DOOD_DOC)
    lowered = dood_text.lower()

    for term in (
        "session container",
        "workload container",
        "runner profile",
        "session-assisted workload",
        "one-shot workload containers",
        "bounded helper containers remain a later phase",
        '`tool.type = "skill"`',
        '`tool.type = "agent_runtime"`',
    ):
        assert term in lowered


def test_tracker_is_listed_in_remaining_work_index() -> None:
    tracker_index_text = _read(TRACKER_INDEX)

    assert "ManagedAgents-DockerOutOfDocker.md" in tracker_index_text
