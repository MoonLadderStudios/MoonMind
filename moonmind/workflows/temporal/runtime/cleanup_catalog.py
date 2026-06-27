"""Declarative catalog for managed-runtime cleanup resource classes.

The catalog is intentionally descriptive. Runtime cleanup implementations own
the side effects; this module gives tests and operators one explicit place to
inspect cleanup boundaries and resource ownership.

MM-947 records the story that introduced this explicit catalog.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedRuntimeCleanupResourceClass:
    name: str
    owner_plane: str
    lifecycle: str
    candidate_source: tuple[str, ...]
    truth_source: tuple[str, ...]
    eligibility: tuple[str, ...]
    safety_behavior: tuple[str, ...]
    deletion_authority: str
    schedule: str
    observability: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagedRuntimeCleanupCatalog:
    resource_classes: tuple[ManagedRuntimeCleanupResourceClass, ...]
    excluded_domains: tuple[str, ...]

    def get(self, name: str) -> ManagedRuntimeCleanupResourceClass:
        for resource_class in self.resource_classes:
            if resource_class.name == name:
                return resource_class
        raise KeyError(name)


MANAGED_RUNTIME_CLEANUP_CATALOG = ManagedRuntimeCleanupCatalog(
    resource_classes=(
        ManagedRuntimeCleanupResourceClass(
            name="managed-session.container",
            owner_plane="managed-session-controller",
            lifecycle="live-runtime",
            candidate_source=("Docker containers with moonmind.session_id label",),
            truth_source=(
                "ManagedSessionStore active records",
                "Temporal owner workflow status for stale active records",
            ),
            eligibility=(
                "session record is absent or terminal",
                "container is older than managed-session reap grace",
                "active record may be forced terminal only when Temporal "
                "owner is terminal",
                "ready session with no active turn may be forced terminal "
                "after max age",
            ),
            safety_behavior=(
                "fail closed when session store is unavailable",
                "skip active sessions",
                "skip containers younger than reap grace",
            ),
            deletion_authority=(
                "DockerCodexManagedSessionController.reap_orphan_session_containers"
            ),
            schedule="MoonMind.ManagedSessionReconcile",
            observability=(
                "scanned_containers",
                "reaped_containers",
                "skipped_active",
                "skipped_recent",
                "forced_stale",
            ),
        ),
        ManagedRuntimeCleanupResourceClass(
            name="managed-session.sidecar-volume",
            owner_plane="managed-session-controller",
            lifecycle="live-runtime",
            candidate_source=("Docker socket and graph volumes for managed sessions",),
            truth_source=(
                "ManagedSessionStore active records",
                "Docker active mounts",
            ),
            eligibility=(
                "volume session id is not active",
                "volume is not mounted by an active container",
                "volume is older than managed-session reap grace",
            ),
            safety_behavior=(
                "skip active mounts",
                "skip active sessions",
                "skip volumes with unknown created-at",
                "skip volumes younger than reap grace",
            ),
            deletion_authority=(
                "DockerCodexManagedSessionController._reap_orphan_sidecar_volumes"
            ),
            schedule="MoonMind.ManagedSessionReconcile",
            observability=(
                "scanned_volumes",
                "reaped_volumes",
                "skipped_active_volumes",
                "skipped_recent_volumes",
            ),
        ),
        ManagedRuntimeCleanupResourceClass(
            name="managed-run.launcher-support-file",
            owner_plane="managed-run-supervisor",
            lifecycle="process-runtime",
            candidate_source=(
                "ManagedRunSupervisor registered cleanup paths",
                "ManagedRunSupervisor deferred cleanup paths",
            ),
            truth_source=(
                "supervised process state",
                "ManagedRunStore active records",
            ),
            eligibility=("run process is canceled, exited, or lost during reconcile",),
            safety_behavior=(
                "best-effort deletion",
                "ignore missing paths",
                "scope to registered cleanup paths only",
            ),
            deletion_authority="ManagedRunSupervisor._cleanup_runtime_files",
            schedule="run lifecycle and supervisor startup reconcile",
        ),
        ManagedRuntimeCleanupResourceClass(
            name="managed-session.skill-projection",
            owner_plane="managed-session-controller",
            lifecycle="session-runtime",
            candidate_source=("ManagedSessionStore workspace path",),
            truth_source=("ManagedSessionStore session record",),
            eligibility=(
                "terminate_session runs",
                "session record is terminal or being terminated",
            ),
            safety_behavior=(
                "scope to owned workspace roots only",
                "best-effort deletion",
            ),
            deletion_authority=(
                "DockerCodexManagedSessionController."
                "_cleanup_skill_projections_for_session"
            ),
            schedule="terminate_session",
        ),
        ManagedRuntimeCleanupResourceClass(
            name="managed-runtime.workspace-root",
            owner_plane="managed-runtime-workspace-janitor",
            lifecycle="retained-state",
            candidate_source=(
                "${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/workspaces/*",
                "${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/${agent_run_id}",
            ),
            truth_source=(
                "ManagedRunStore all records",
                "ManagedSessionStore all records",
                "Docker active containers and volume mounts",
                "canonical managed-runtime filesystem layout",
            ),
            eligibility=(
                "every referencing run is terminal",
                "every referencing session is terminal",
                "no referencing record has activeTurnId",
                "newest owner activity is older than workspace retention and grace",
                "no live Docker resource references the session or workspace",
            ),
            safety_behavior=(
                "dry-run by default",
                "canonical paths only",
                "skip symlinks",
                "skip when any owner is active",
                "skip when any owner is recent",
                "skip when ownership is ambiguous",
                "rescan before delete",
                "janitor lock required",
            ),
            deletion_authority="ManagedRuntimeWorkspaceJanitor",
            schedule="MoonMind.ManagedRuntimeWorkspaceCleanup",
            observability=(
                "scanned_workspace_roots",
                "protected_roots",
                "eligible_roots",
                "deleted_roots",
                "skipped_active",
                "skipped_recent",
                "skipped_unsafe_path",
                "skipped_ambiguous_owner",
            ),
        ),
        ManagedRuntimeCleanupResourceClass(
            name="managed-runtime.artifact-dir",
            owner_plane="managed-runtime-workspace-janitor",
            lifecycle="retained-state",
            candidate_source=("normalized managed_runtime_artifact_root() children",),
            truth_source=(
                "ManagedRunStore artifact refs",
                "ManagedSessionStore artifact refs",
            ),
            eligibility=(
                "all referencing runs and sessions are terminal",
                "artifact retention window has elapsed",
                "no retained record still needs the directory for UI, log, "
                "or audit lookup",
            ),
            safety_behavior=(
                "retain longer than workspaces",
                "skip when referenced by retained record",
                "dry-run by default",
            ),
            deletion_authority="ManagedRuntimeWorkspaceJanitor",
            schedule="MoonMind.ManagedRuntimeWorkspaceCleanup",
            observability=(
                "scanned_artifact_dirs",
                "deleted_artifact_dirs",
                "skipped_recent",
            ),
        ),
        ManagedRuntimeCleanupResourceClass(
            name="managed-runtime.record",
            owner_plane="managed-runtime-workspace-janitor",
            lifecycle="retained-metadata",
            candidate_source=(
                "${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/managed_runs/*.json",
                "${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/"
                "managed_sessions/*.json",
            ),
            truth_source=("record JSON status and timestamps",),
            eligibility=(
                "record is terminal",
                "record retention window has elapsed",
                "referenced workspace and artifacts were already deleted or "
                "intentionally retained",
            ),
            safety_behavior=(
                "delete only after workspace and artifact passes",
                "optional feature",
                "dry-run by default",
            ),
            deletion_authority="ManagedRuntimeWorkspaceJanitor",
            schedule="MoonMind.ManagedRuntimeWorkspaceCleanup",
            observability=("deleted_record_files",),
        ),
    ),
    excluded_domains=(
        "database execution records",
        "Temporal workflow histories",
        "long-term artifact service retention outside the managed runtime "
        "local filesystem",
        "provider-profile credentials and OAuth auth volumes",
        "memory indexes",
        "deployment desired-state files",
        "application logs outside the managed-runtime workspace root",
    ),
)


__all__ = (
    "MANAGED_RUNTIME_CLEANUP_CATALOG",
    "ManagedRuntimeCleanupCatalog",
    "ManagedRuntimeCleanupResourceClass",
)
