from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DOOD_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "DockerOutOfDocker.md"
BACKEND_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "DockerBackendService.md"
SIDECAR_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "DockerSidecarRuntime.md"
ARCH_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "ManagedAgentArchitecture.md"
SESSION_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "CodexCliManagedSessions.md"
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


def test_canonical_dood_doc_does_not_link_removed_phase0_tracker() -> None:
    dood_text = _read(DOOD_DOC)

    assert "../tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md" not in dood_text
    assert not TRACKER.exists()


def test_docker_backend_service_is_the_canonical_declarative_design() -> None:
    text = _read(BACKEND_DOC)

    assert "# Docker Backend Service" in text
    assert "Document class:** Canonical declarative design" in text
    assert "part of the MoonMind API subsystem" in text
    assert "Temporal owns the durable execution interval" in text
    assert "existing system Docker daemon" in text
    assert "No separate MoonMind workload daemon is required" in text


def test_docker_backend_service_exposes_asynchronous_agent_tools() -> None:
    text = _read(BACKEND_DOC)

    for tool_name in (
        "`container.submit`",
        "`container.status`",
        "`container.logs`",
        "`container.artifacts`",
        "`container.cancel`",
    ):
        assert tool_name in text

    assert "Agents do not receive Docker authority" in text
    assert "raw Docker CLI execution remains" in text


def test_docker_backend_service_reuses_images_across_workflows() -> None:
    text = _read(BACKEND_DOC).lower()

    for term in (
        "cross-workflow image reuse",
        "images are acquired on demand",
        "cache survives",
        "workflow cleanup never removes shared images",
        "pull locking",
        "removeimagesonterminal: false",
    ):
        assert term in text


def test_docker_backend_service_declares_omnigent_integration() -> None:
    text = _read(BACKEND_DOC)

    assert "## 18. Omnigent integration" in text
    assert "http://api:8000/mcp" in text
    assert "omnigent-host" in text
    assert "do not need" in text
    assert "the Docker CLI" in text
    assert "a mounted Docker socket" in text


def test_sidecar_document_is_only_a_supersession_pointer() -> None:
    text = _read(SIDECAR_DOC)

    assert "Status:** Superseded" in text
    assert "DockerBackendService.md" in text
    assert "no longer the MoonMind desired state" in text
    assert "## 1. Purpose" not in text
    assert "kind: ManagedAgentRuntimeProfile" not in text


def test_execution_model_keeps_docker_workloads_on_tool_path() -> None:
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
        "one-shot workload tool",
        "profile-backed helper containers remain explicitly owned",
        '`tool.type = "skill"`',
        "`agent_runtime.*`",
        "`moonmind.agentrun`",
    ):
        assert term in lowered


def test_tracker_is_listed_in_remaining_work_index() -> None:
    if not TRACKER_INDEX.exists():
        return
    tracker_index_text = _read(TRACKER_INDEX)

    assert "ManagedAgents-DockerOutOfDocker.md" in tracker_index_text
