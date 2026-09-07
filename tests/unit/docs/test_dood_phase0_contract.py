from pathlib import Path

from ._doc_assert import assert_semantic_keywords, assert_semantic_phrase


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "DockerBackendService.md"
DOOD_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "DockerOutOfDocker.md"
SIDECAR_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "DockerSidecarRuntime.md"
ARCH_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "ManagedAgentArchitecture.md"
SESSION_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "CodexCliManagedSessions.md"
PLATFORM_DOC = REPO_ROOT / "docs" / "MoonMindArchitecture.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_docker_backend_service_is_api_owned_and_temporal_backed() -> None:
    text = _read(BACKEND_DOC)

    assert_semantic_keywords(
        text,
        ("docker backend service",),
        path=BACKEND_DOC,
    )
    assert_semantic_phrase(text, "part of the MoonMind API subsystem", path=BACKEND_DOC)
    assert_semantic_phrase(text, "Temporal owns long-running execution", path=BACKEND_DOC)
    assert_semantic_phrase(text, "existing system Docker daemon", path=BACKEND_DOC)
    # Semantic contract: no dedicated daemon is required. Wording may change;
    # the denial of a dedicated daemon must not.
    assert_semantic_keywords(
        text,
        ("no dedicated", "docker daemon", "required"),
        path=BACKEND_DOC,
    )


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

    assert_semantic_phrase(text, "Docker CLI execution remains", path=BACKEND_DOC)
    assert "rawDockerCliExposedToAgents: false" in text


def test_docker_backend_service_reuses_provisioned_images_across_workflows() -> None:
    text = _read(BACKEND_DOC)

    assert_semantic_phrase(text, "Optional images are acquired on demand", path=BACKEND_DOC)
    assert_semantic_phrase(
        text,
        "Deployment-owned local image recipes are provisioned on demand",
        path=BACKEND_DOC,
    )
    assert_semantic_phrase(text, "cross-workflow image cache", path=BACKEND_DOC)
    assert "reusableAcrossWorkflows: true" in text
    assert "removeOnJobEnd: false" in text
    assert_semantic_phrase(text, "per-source, per-build-key lock", path=BACKEND_DOC)
    assert_semantic_phrase(text, "Job cleanup never removes shared images", path=BACKEND_DOC)


def test_docker_backend_service_uses_logical_workspace_references() -> None:
    text = _read(BACKEND_DOC)

    assert_semantic_phrase(text, "Workspaces are logical references", path=BACKEND_DOC)
    assert "callerProvidesHostPath: false" in text
    assert "visibilityProbeBeforeProvisioning: true" in text
    # Semantic contract: a failed probe stops the job before provisioning.
    assert_semantic_keywords(
        text,
        ("failed probe", "stops the job", "provisioning"),
        path=BACKEND_DOC,
    )


def test_omnigent_uses_mcp_without_receiving_docker_authority() -> None:
    text = _read(BACKEND_DOC)

    assert_semantic_phrase(
        text, "Omnigent and MoonMind managed sessions use the same tools", path=BACKEND_DOC
    )
    assert_semantic_phrase(text, "does not need a Docker CLI", path=BACKEND_DOC)
    assert "session-local `DOCKER_HOST`" in text
    assert "omnigent-session" in text


def test_canonical_backend_doc_has_no_migration_or_compatibility_checklist() -> None:
    text = _read(BACKEND_DOC).lower()

    assert "## migration" not in text
    assert "migration from per-session" not in text
    assert "temporary compatibility" not in text
    assert "while callers migrate" not in text


def test_sidecar_document_is_only_a_removed_design_tombstone() -> None:
    text = _read(SIDECAR_DOC)

    # Tombstone semantics: removed, not supported, no runtime mode. Exact
    # cosmetic wording is intentionally not pinned.
    assert_semantic_keywords(
        text,
        ("removed from desired state",),
        path=SIDECAR_DOC,
    )
    assert_semantic_keywords(
        text,
        ("not a supported", "desired state", "compatibility path"),
        path=SIDECAR_DOC,
    )
    assert_semantic_keywords(
        text,
        ("tombstone", "old links"),
        path=SIDECAR_DOC,
    )
    assert_semantic_keywords(
        text,
        ("does not define a runtime mode",),
        path=SIDECAR_DOC,
    )
    assert "may remain temporarily" not in text


def test_dood_document_is_only_a_consolidation_tombstone() -> None:
    text = _read(DOOD_DOC)

    assert_semantic_phrase(text, "Consolidated into", path=DOOD_DOC)
    assert_semantic_phrase(text, "Docker Backend Service", path=DOOD_DOC)
    assert_semantic_keywords(
        text,
        ("does not define a parallel workload architecture",),
        path=DOOD_DOC,
    )


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

    assert_semantic_phrase(text, "The core remains workload-agnostic", path=BACKEND_DOC)
    assert "not backend" in text
    assert_semantic_phrase(
        text, "branches for Python, .NET, Unreal, Unity, or Node", path=BACKEND_DOC
    )
    assert_semantic_phrase(text, "specialized worker pool", path=BACKEND_DOC)
