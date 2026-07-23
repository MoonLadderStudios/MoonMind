from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "DockerBackendService.md"
DOOD_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "DockerOutOfDocker.md"
SIDECAR_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "DockerSidecarRuntime.md"
ARCH_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "ManagedAgentArchitecture.md"
SESSION_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "CodexCliManagedSessions.md"
PLATFORM_DOC = REPO_ROOT / "docs" / "MoonMindArchitecture.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).split())


def test_docker_backend_service_is_api_owned_and_temporal_backed() -> None:
    text = _read(BACKEND_DOC)

    assert "Docker Backend Service" in text
    assert "part of the MoonMind API subsystem" in text
    assert "Temporal owns long-running execution" in text
    assert "existing system Docker daemon" in text
    assert "No dedicated MoonMind Docker daemon is required today" in text


def test_docker_backend_service_exposes_asynchronous_agent_tools() -> None:
    text = _read(BACKEND_DOC)

    for tool_name in (
        "container.submit",
        "container.status",
        "container.logs",
        "container.artifacts",
        "container.cancel",
    ):
        assert tool_name in text

    assert "Docker CLI execution remains" in text
    assert "rawDockerCliExposedToAgents: false" in text


def test_docker_backend_service_reuses_provisioned_images_across_workflows() -> None:
    text = _read(BACKEND_DOC)

    assert "Optional images are acquired on demand" in text
    assert "Deployment-owned local image recipes are provisioned on demand" in text
    assert "cross-workflow image cache" in text
    assert "reusableAcrossWorkflows: true" in text
    assert "removeOnJobEnd: false" in text
    assert "per-source, per-build-key lock" in text
    assert "Job cleanup never removes shared images" in text


def test_docker_backend_service_uses_logical_workspace_references() -> None:
    text = _read(BACKEND_DOC)

    assert "Workspaces are logical references" in text
    assert "callerProvidesHostPath: false" in text
    assert "visibilityProbeBeforeProvisioning: true" in text
    assert "A failed probe stops the job before expensive image provisioning" in text


def test_omnigent_uses_mcp_without_receiving_docker_authority() -> None:
    text = _read(BACKEND_DOC)

    assert "Omnigent and MoonMind managed sessions use the same tools" in text
    assert "does not need a Docker CLI" in text
    assert "session-local `DOCKER_HOST`" in text
    assert "omnigent-session" in text


def test_canonical_backend_doc_has_no_migration_or_compatibility_checklist() -> None:
    text = _read(BACKEND_DOC).lower()

    assert "## migration" not in text
    assert "migration from per-session" not in text
    assert "temporary compatibility" not in text
    assert "while callers migrate" not in text


def test_sidecar_document_is_only_a_removed_design_tombstone() -> None:
    text = _normalized(SIDECAR_DOC)

    assert "Removed from desired state" in text
    assert "not a supported MoonMind desired state or compatibility path" in text
    assert "only a tombstone for old links" in text
    assert "does not define a runtime mode" in text
    assert "may remain temporarily" not in text


def test_dood_document_is_only_a_consolidation_tombstone() -> None:
    text = _normalized(DOOD_DOC)

    assert "Consolidated into" in text
    assert "Docker Backend Service" in text
    assert "does not define a parallel workload architecture" in text


def test_related_architecture_docs_do_not_reinstate_sidecar_as_default() -> None:
    forbidden = (
        "per-session Docker sidecar",
        "ordinary managed-session Docker work uses a per-session sidecar",
        "ordinary repository test workloads use the sidecar",
        "default way to provide it is the per-session Docker sidecar",
    )

    for path in (ARCH_DOC, SESSION_DOC, PLATFORM_DOC):
        contents = _read(path)
        lowered = contents.lower()
        assert "DockerBackendService.md" in contents, path
        for phrase in forbidden:
            assert phrase.lower() not in lowered, path


def test_backend_doc_remains_domain_agnostic() -> None:
    text = _read(BACKEND_DOC)

    assert "The core remains workload-agnostic" in text
    assert "not backend" in text
    assert "branches for Python, .NET, Unreal, Unity, or Node" in text
    assert "specialized worker pool" in text
