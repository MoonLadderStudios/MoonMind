import pytest

from moonmind.workflows.temporal.runtime.cleanup_catalog import (
    MANAGED_RUNTIME_CLEANUP_CATALOG,
)


def test_cleanup_catalog_declares_managed_runtime_resource_classes():
    names = {
        resource_class.name
        for resource_class in MANAGED_RUNTIME_CLEANUP_CATALOG.resource_classes
    }

    assert names == {
        "managed-session.container",
        "managed-session.sidecar-volume",
        "managed-run.launcher-support-file",
        "managed-session.skill-projection",
        "managed-runtime.workspace-root",
        "managed-runtime.artifact-dir",
        "managed-runtime.record",
    }


@pytest.mark.parametrize(
    "resource_name",
    [
        "managed-session.container",
        "managed-session.sidecar-volume",
        "managed-run.launcher-support-file",
        "managed-session.skill-projection",
        "managed-runtime.workspace-root",
        "managed-runtime.artifact-dir",
        "managed-runtime.record",
    ],
)
def test_cleanup_resource_classes_have_required_metadata(resource_name):
    resource_class = MANAGED_RUNTIME_CLEANUP_CATALOG.get(resource_name)

    assert resource_class.owner_plane
    assert resource_class.lifecycle
    assert resource_class.candidate_source
    assert resource_class.truth_source
    assert resource_class.eligibility
    assert resource_class.safety_behavior
    assert resource_class.deletion_authority
    assert resource_class.schedule


def test_cleanup_catalog_preserves_live_runtime_and_retained_state_boundary():
    live_cleanup = {
        resource_class.name: resource_class
        for resource_class in MANAGED_RUNTIME_CLEANUP_CATALOG.resource_classes
        if resource_class.lifecycle
        in {"live-runtime", "process-runtime", "session-runtime"}
    }
    retained_cleanup = {
        resource_class.name: resource_class
        for resource_class in MANAGED_RUNTIME_CLEANUP_CATALOG.resource_classes
        if resource_class.lifecycle in {"retained-state", "retained-metadata"}
    }

    assert {
        name
        for name, resource_class in live_cleanup.items()
        if resource_class.deletion_authority == "ManagedRuntimeWorkspaceJanitor"
    } == set()
    assert {
        name
        for name, resource_class in retained_cleanup.items()
        if "ManagedSessionReconcile" in resource_class.schedule
        or "terminate_session" in resource_class.schedule
        or "supervisor" in resource_class.schedule
    } == set()
    assert retained_cleanup["managed-runtime.workspace-root"].schedule == (
        "MoonMind.ManagedRuntimeWorkspaceCleanup"
    )
    assert retained_cleanup["managed-runtime.artifact-dir"].schedule == (
        "MoonMind.ManagedRuntimeWorkspaceCleanup"
    )


def test_cleanup_catalog_encodes_excluded_non_goal_domains():
    excluded_domains = set(MANAGED_RUNTIME_CLEANUP_CATALOG.excluded_domains)

    assert excluded_domains == {
        "database execution records",
        "Temporal workflow histories",
        "long-term artifact service retention outside the managed runtime "
        "local filesystem",
        "provider-profile credentials and OAuth auth volumes",
        "memory indexes",
        "deployment desired-state files",
        "application logs outside the managed-runtime workspace root",
    }


def test_retained_state_cleanup_uses_all_records_and_dry_run_safety():
    workspace_root = MANAGED_RUNTIME_CLEANUP_CATALOG.get(
        "managed-runtime.workspace-root"
    )
    artifact_dir = MANAGED_RUNTIME_CLEANUP_CATALOG.get("managed-runtime.artifact-dir")
    record = MANAGED_RUNTIME_CLEANUP_CATALOG.get("managed-runtime.record")

    assert "ManagedRunStore all records" in workspace_root.truth_source
    assert "ManagedSessionStore all records" in workspace_root.truth_source
    assert "dry-run by default" in workspace_root.safety_behavior
    assert "dry-run by default" in artifact_dir.safety_behavior
    assert "dry-run by default" in record.safety_behavior
