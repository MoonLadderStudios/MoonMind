#!/usr/bin/env python3
"""Select backend and frontend CI test suites from changed paths.

The selector is intentionally conservative: empty input, CI/test/dependency
changes, main branch pushes, scheduled runs, and manual dispatches all select
the full backend path.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

OUTPUT_KEYS = (
    "unit_fast",
    "unit_slow",
    "api_component",
    "temporal_boundary",
    "integration_ci",
    "reliability_journey",
    "exact_artifact",
    "omnigent_conformance",
    "full_backend",
    "frontend_static",
    "frontend_browser_chromium",
    "frontend_browser_firefox",
    "full_frontend",
)

FRONTEND_STATIC_EXACT = {
    "package.json",
    "package-lock.json",
    "postcss.config.cjs",
    "tailwind.config.cjs",
    "tools/test_unit.sh",
    "tools/run_repo_python.sh",
    "tools/verify_vite_manifest.py",
    "tools/export_openapi.py",
    "tools/generate_openapi_types.py",
}
FRONTEND_STATIC_PREFIXES = ("frontend/", "api_service/templates/")
FRONTEND_CHROMIUM_PREFIXES = ("frontend/src/", "api_service/templates/")
FRONTEND_FIREFOX_EXACT = {
    "package.json",
    "package-lock.json",
    "frontend/vitest.browser.config.ts",
}
FRONTEND_FIREFOX_PREFIXES = ("frontend/src/browser/", "frontend/src/styles/")

FORCE_FULL_EXACT = {
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "tools/test_unit.sh",
    "tools/test_unit_docker.sh",
    "tools/test_integration.sh",
    "tools/select_test_suites.py",
    "tests/conftest.py",
    "tests/unit/conftest.py",
}

FORCE_FULL_PREFIXES = (".github/workflows/",)

API_COMPONENT_EXACT = {
    "api_service/auth_providers.py",
    "tools/export_openapi.py",
    "tools/generate_openapi_types.py",
    "frontend/src/generated/openapi.ts",
}

API_COMPONENT_PREFIXES = (
    "api_service/api/",
    "api_service/db/",
    "api_service/services/",
    "tests/unit/api/",
    "tests/unit/api_service/",
    "tests/component/api/",
)

API_COMPONENT_GLOBS = ("api_service/auth*",)

TEMPORAL_BOUNDARY_EXACT = {
    "moonmind/schemas/managed_session_models.py",
}

TEMPORAL_BOUNDARY_PREFIXES = (
    "moonmind/workflows/adapters/",
    "moonmind/workflows/temporal/",
    "tests/unit/workflows/adapters/",
    "tests/unit/workflows/temporal/",
    "tests/integration/workflows/temporal/",
)

TEMPORAL_BOUNDARY_GLOBS = (
    "moonmind/schemas/*workflow*",
    "moonmind/schemas/*temporal*",
    "api_service/worker*",
)

INTEGRATION_CI_EXACT = {
    "docker-compose.test.yaml",
    "api_service/Dockerfile",
    ".env-template",
    "tools/test_integration.sh",
    "pyproject.toml",
    "uv.lock",
}

INTEGRATION_CI_PREFIXES = (
    "tests/integration/",
    "api_service/db/",
    "api_service/migrations/",
    "migrations/",
    "alembic/",
)

INTEGRATION_CI_EXCLUDED_PREFIXES = ("tests/integration/reliability/",)

UNIT_SLOW_PREFIXES = ("tests/unit/api/routers/test_agent_runs.py",)

RELIABILITY_JOURNEY_EXACT = {
    ".github/workflows/pytest-unit-tests.yml",
    "api_service/Dockerfile",
    "docker-compose.test.yaml",
    "moonmind/schemas/agent_runtime_models.py",
    "moonmind/schemas/managed_session_models.py",
    "moonmind/schemas/temporal_models.py",
    "tests/helpers/codex_session_runtime.py",
    "tools/select_test_suites.py",
    "tools/start-worker.sh",
}

RELIABILITY_JOURNEY_PREFIXES = (
    ".agents/skills/",
    "api_service/docker/",
    "api_service/api/routers/workflows",
    "api_service/services/artifact",
    "api_service/services/checkpoint_",
    "api_service/services/managed_agent_provider",
    "api_service/services/omnigent",
    "api_service/services/workspace",
    "docker/",
    "moonmind/agents/codex_worker/",
    "moonmind/omnigent/",
    "moonmind/workflows/adapters/",
    "moonmind/workflows/temporal/",
    "tests/integration/omnigent/",
    "tests/integration/reliability/",
)

RELIABILITY_JOURNEY_GLOBS = (
    "moonmind/schemas/*checkpoint*",
    "moonmind/schemas/*recovery*",
    "moonmind/schemas/*workspace*",
    "api_service/migrations/**/*checkpoint*",
)

# --- Omnigent contract-owner inventory (MoonLadderStudios/MoonMind#3710) ---
#
# A single, tested inventory of the paths that own the Omnigent production
# contract.  A change to any owned path must select the *complete* Omnigent
# contract gate (the cross-layer required jobs below) rather than only the
# suite that happens to match the directly-changed file's directory.  This
# closes the gap where API-component, Temporal-boundary, or integration jobs
# were silently skipped by changed-file selection for an Omnigent change.
#
# Selection is based on contract ownership and dependency edges: the owned
# entrypoints (core, Temporal, API, native-UI, compiled-intent schemas,
# fixtures, and conformance tooling), plus the deployment/dependency edges
# (Dockerfiles, Compose, startup scripts, dependency locks, and CI/selection
# definitions) which are already routed to the full backend gate elsewhere in
# this selector.
OMNIGENT_CONTRACT_GATE_KEYS = (
    "unit_fast",
    "api_component",
    "temporal_boundary",
    "integration_ci",
    "reliability_journey",
    # An Omnigent-owned change ships in the deployable API/worker/UI images, so
    # it must also exercise the Tier-1 exact deployable-artifact gate.
    "exact_artifact",
    # The deterministic conformance runner republishes the Omnigent evidence
    # bundle from the same layers; it is part of the owned contract gate rather
    # than an unconditional job, so unrelated changes do not pay for it.
    "omnigent_conformance",
)

OMNIGENT_CONTRACT_EXACT = {
    # Omnigent runtime schemas / compiled-intent contracts.
    "moonmind/schemas/workspace_intent.py",
    # Omnigent Temporal activities and workflows.
    "moonmind/workflows/temporal/activities/omnigent_activities.py",
    # Frontend Workflow Detail / Workflow Chat integration entrypoints.
    "frontend/src/entrypoints/WorkflowChatNative.tsx",
    "frontend/src/entrypoints/WorkflowChatNative.test.tsx",
    "frontend/src/entrypoints/workflow-detail.tsx",
    "frontend/src/entrypoints/omnigent-inventory.tsx",
    "frontend/src/lib/workflowDetailRoutes.ts",
    "frontend/src/lib/workflowDetailRoutes.test.ts",
    # Omnigent conformance / fault fixture tooling.
    "tools/run_omnigent_browser_journey.mjs",
}

OMNIGENT_CONTRACT_PREFIXES = (
    "moonmind/omnigent/",
    "api_service/services/omnigent",
    "api_service/api/routers/omnigent",
    "tests/integration/omnigent/",
    "tests/unit/omnigent/",
    "frontend/src/features/workflow-native-chat/",
)

OMNIGENT_CONTRACT_GLOBS = (
    "moonmind/workflows/adapters/omnigent_*",
    "moonmind/workflows/temporal/workflows/omnigent_*",
    "tools/run_omnigent_*",
    "tools/build_omnigent_*",
)

# Inputs of the deterministic conformance runner that live outside the owned
# inventory above: the pytest layers and frontend test it executes, its profile,
# and its report builder. A change to any of them must republish the evidence
# bundle, but it does not by itself elevate the complete contract gate; the
# shard that owns the file still runs it. tests/unit/tools cross-checks this set
# against ``tools/run_omnigent_conformance.py``.
OMNIGENT_CONFORMANCE_INPUT_EXACT = {
    "frontend/src/entrypoints/workflow-detail.test.tsx",
    "tests/fixtures/omnigent/conformance-v4.json",
    "tests/integration/reliability/test_checkpoint_cold_resume.py",
    "tests/integration/reliability_journey/"
    "test_omnigent_cumulative_remediation_journey.py",
    "tests/unit/tools/test_run_omnigent_live_conformance.py",
    "tests/unit/workflows/adapters/test_external_adapter_registry.py",
    "tests/unit/workflows/temporal/test_remediation_workspace_head.py",
    "tests/unit/workflows/temporal/test_temporal_workers.py",
    "tests/unit/workflows/temporal/workflows/test_run_bounded_story_loop.py",
    "tests/unit/workflows/temporal/workflows/test_run_integration.py",
}

# The subset of owned paths whose change can affect the compiled native UI or
# facade behavior, which must additionally exercise the compiled production
# browser suite (issue #3710 required PR gate item 7).
OMNIGENT_FACADE_EXACT = {
    "moonmind/omnigent/native_ui.py",
    "moonmind/omnigent/native_ui_compat.py",
    "moonmind/omnigent/workflow_chat_facade.py",
    "moonmind/omnigent/native_outbound_scan.py",
    "api_service/api/routers/omnigent_native_ui.py",
    "api_service/api/routers/omnigent_catalog.py",
    "frontend/src/entrypoints/WorkflowChatNative.tsx",
    "frontend/src/entrypoints/WorkflowChatNative.test.tsx",
    "frontend/src/entrypoints/workflow-detail.tsx",
    "frontend/src/entrypoints/omnigent-inventory.tsx",
    "frontend/src/lib/workflowDetailRoutes.ts",
    "frontend/src/lib/workflowDetailRoutes.test.ts",
}

OMNIGENT_FACADE_PREFIXES = ("frontend/src/features/workflow-native-chat/",)

# --- Tier-1 exact deployable-artifact gate (MoonLadderStudios/MoonMind#3710) ---
#
# The required, noncredentialed exact-artifact conformance gate builds or pulls
# the exact deployable API/worker/UI images and tests them by immutable digest
# through their real entrypoints.  A change to the *runtime capability surface*
# of those images — dependency locks, Dockerfiles, Compose, startup scripts and
# runtime entrypoints, or the exact-artifact gate tooling itself — must always
# select this gate, because a source-level test passing does not prove the
# deployed process retains a required runtime capability (see the missing
# Uvicorn WebSocket implementation in #3697).
EXACT_ARTIFACT_EXACT = {
    # Dependency and lockfile changes must always select this gate.
    "pyproject.toml",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    # Startup scripts and runtime entrypoints.  ``api_service/entrypoint.sh``
    # is the production API command installed as the image ``CMD``, so a change
    # to how Uvicorn is launched must always select this gate.  Compose files
    # are covered by the ``docker-compose*`` globs below.
    ".env-template",
    "api_service/entrypoint.sh",
    "tools/start-worker.sh",
    # The exact-artifact gate implementation itself.
    "moonmind/omnigent/exact_artifact_conformance.py",
    "tools/omnigent_exact_artifact_probe.py",
    "tools/run_omnigent_exact_artifact_conformance.py",
}

EXACT_ARTIFACT_PREFIXES = (
    "api_service/docker/",
    "docker/",
)

EXACT_ARTIFACT_GLOBS = (
    # Any Dockerfile at any depth, plus named *.Dockerfile variants.
    "Dockerfile",
    "*.Dockerfile",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "tools/omnigent_exact_artifact_*",
    "tools/run_omnigent_exact_artifact_*",
)

BACKEND_PREFIXES = (
    ".agents/skills/",
    "api_service/",
    "docker/",
    "moonmind/",
    "tests/unit/",
    "tests/integration/",
    "tests/contract/",
    "tests/api_service/",
    "tests/component/",
    "tools/",
    "migrations/",
    "alembic/",
)

NON_BACKEND_PREFIXES = (
    "docs/",
    "frontend/",
)

NON_BACKEND_EXACT = {
    "AGENTS.md",
    "README.md",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "vite.config.ts",
}


@dataclass(frozen=True)
class SuiteSelection:
    unit_fast: bool = False
    unit_slow: bool = False
    api_component: bool = False
    temporal_boundary: bool = False
    integration_ci: bool = False
    reliability_journey: bool = False
    exact_artifact: bool = False
    omnigent_conformance: bool = False
    full_backend: bool = False
    frontend_static: bool = False
    frontend_browser_chromium: bool = False
    frontend_browser_firefox: bool = False
    full_frontend: bool = False

    def as_outputs(self) -> dict[str, str]:
        return {key: "true" if getattr(self, key) else "false" for key in OUTPUT_KEYS}


def _normalize_path(raw_path: str) -> str | None:
    path = raw_path.strip()
    if not path:
        return None
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    normalized = PurePosixPath(path).as_posix()
    if normalized == "." or normalized.startswith("../") or normalized == "..":
        return None
    return normalized


def _matches(path: str, *, exact=(), prefixes=(), globs=()) -> bool:
    posix_path = PurePosixPath(path)
    return (
        path in exact
        or any(path.startswith(prefix) for prefix in prefixes)
        or any(posix_path.match(pattern) for pattern in globs)
    )


def _is_force_full_event(event_name: str | None, ref_name: str | None) -> bool:
    if event_name in {"workflow_dispatch", "schedule"}:
        return True
    return event_name == "push" and ref_name in {"main", "refs/heads/main"}


def _is_non_backend_path(path: str) -> bool:
    return path in NON_BACKEND_EXACT or any(
        path.startswith(prefix) for prefix in NON_BACKEND_PREFIXES
    )


def _is_backend_path(path: str) -> bool:
    return (
        path in FORCE_FULL_EXACT
        or path in OMNIGENT_CONFORMANCE_INPUT_EXACT
        or path in API_COMPONENT_EXACT
        or path in TEMPORAL_BOUNDARY_EXACT
        or path in INTEGRATION_CI_EXACT
        or path in RELIABILITY_JOURNEY_EXACT
        or any(path.startswith(prefix) for prefix in BACKEND_PREFIXES)
        or _matches(
            path,
            globs=(
                API_COMPONENT_GLOBS
                + TEMPORAL_BOUNDARY_GLOBS
                + RELIABILITY_JOURNEY_GLOBS
            ),
        )
    )


def is_omnigent_contract_owned(path: str) -> bool:
    """Return whether a changed path owns the Omnigent production contract."""
    return _matches(
        path,
        exact=OMNIGENT_CONTRACT_EXACT,
        prefixes=OMNIGENT_CONTRACT_PREFIXES,
        globs=OMNIGENT_CONTRACT_GLOBS,
    )


def is_exact_artifact_owned(path: str) -> bool:
    """Return whether a change must select the Tier-1 exact-artifact gate."""
    return _matches(
        path,
        exact=EXACT_ARTIFACT_EXACT,
        prefixes=EXACT_ARTIFACT_PREFIXES,
        globs=EXACT_ARTIFACT_GLOBS,
    )


def is_omnigent_conformance_input(path: str) -> bool:
    """Return whether a changed path feeds the deterministic conformance runner."""
    return is_omnigent_contract_owned(path) or path in OMNIGENT_CONFORMANCE_INPUT_EXACT


def _is_omnigent_facade_path(path: str) -> bool:
    """Return whether an owned path can affect the compiled native UI/facade."""
    return _matches(
        path,
        exact=OMNIGENT_FACADE_EXACT,
        prefixes=OMNIGENT_FACADE_PREFIXES,
    )


def _elevate_omnigent_contract_gate(
    selection: SuiteSelection, omnigent_paths: Iterable[str]
) -> SuiteSelection:
    """Force the complete Omnigent contract gate for an owned change."""
    updates = {key: True for key in OMNIGENT_CONTRACT_GATE_KEYS}
    if any(_is_omnigent_facade_path(path) for path in omnigent_paths):
        updates["frontend_static"] = True
        updates["frontend_browser_chromium"] = True
    merged = {
        key: getattr(selection, key) or updates.get(key, False)
        for key in selection.__dict__
    }
    return SuiteSelection(**merged)


def _full_backend_selection() -> SuiteSelection:
    return SuiteSelection(
        unit_fast=True,
        unit_slow=True,
        api_component=True,
        temporal_boundary=True,
        integration_ci=True,
        reliability_journey=True,
        exact_artifact=True,
        omnigent_conformance=True,
        full_backend=True,
    )


def _full_selection() -> SuiteSelection:
    return SuiteSelection(
        **{
            **_full_backend_selection().__dict__,
            "frontend_static": True,
            "frontend_browser_chromium": True,
            "frontend_browser_firefox": True,
            "full_frontend": True,
        }
    )


def select_suites(
    changed_files: Iterable[str],
    *,
    event_name: str | None = None,
    ref_name: str | None = None,
) -> SuiteSelection:
    paths = [
        path
        for raw_path in changed_files
        if (path := _normalize_path(raw_path)) is not None
    ]

    if _is_force_full_event(event_name, ref_name):
        return _full_selection()
    if not paths:
        return _full_selection()
    if any(
        _matches(path, exact=FORCE_FULL_EXACT, prefixes=FORCE_FULL_PREFIXES)
        for path in paths
    ):
        return _full_selection()

    unknown_paths = [
        path
        for path in paths
        if not _is_backend_path(path) and not _is_non_backend_path(path)
    ]
    if unknown_paths:
        return _full_selection()

    backend_paths = [path for path in paths if _is_backend_path(path)]
    static = any(
        _matches(path, exact=FRONTEND_STATIC_EXACT, prefixes=FRONTEND_STATIC_PREFIXES)
        for path in paths
    )
    firefox = any(
        _matches(path, exact=FRONTEND_FIREFOX_EXACT, prefixes=FRONTEND_FIREFOX_PREFIXES)
        for path in paths
    )
    chromium = firefox or any(
        path != "frontend/src/generated/openapi.ts"
        and _matches(path, prefixes=FRONTEND_CHROMIUM_PREFIXES)
        for path in paths
    )
    full_frontend = any(path in {"package.json", "package-lock.json"} for path in paths)

    selection = SuiteSelection(
        unit_fast=bool(backend_paths),
        unit_slow=any(
            _matches(path, prefixes=UNIT_SLOW_PREFIXES) for path in backend_paths
        ),
        api_component=any(
            _matches(
                path,
                exact=API_COMPONENT_EXACT,
                prefixes=API_COMPONENT_PREFIXES,
                globs=API_COMPONENT_GLOBS,
            )
            for path in backend_paths
        ),
        temporal_boundary=any(
            _matches(
                path,
                exact=TEMPORAL_BOUNDARY_EXACT,
                prefixes=TEMPORAL_BOUNDARY_PREFIXES,
                globs=TEMPORAL_BOUNDARY_GLOBS,
            )
            for path in backend_paths
        ),
        integration_ci=any(
            _matches(
                path,
                exact=INTEGRATION_CI_EXACT,
                prefixes=INTEGRATION_CI_PREFIXES,
            )
            and not _matches(path, prefixes=INTEGRATION_CI_EXCLUDED_PREFIXES)
            for path in backend_paths
        ),
        reliability_journey=any(
            _matches(
                path,
                exact=RELIABILITY_JOURNEY_EXACT,
                prefixes=RELIABILITY_JOURNEY_PREFIXES,
                globs=RELIABILITY_JOURNEY_GLOBS,
            )
            for path in backend_paths
        ),
        # Computed over every path (not just backend paths): dependency locks
        # and frontend Dockerfiles are non-backend but still change the
        # deployable image runtime surface.
        exact_artifact=any(is_exact_artifact_owned(path) for path in paths),
        omnigent_conformance=any(is_omnigent_conformance_input(path) for path in paths),
        frontend_static=static or chromium,
        frontend_browser_chromium=chromium,
        frontend_browser_firefox=firefox,
        full_frontend=full_frontend,
    )

    omnigent_paths = [path for path in paths if is_omnigent_contract_owned(path)]
    if omnigent_paths:
        selection = _elevate_omnigent_contract_gate(selection, omnigent_paths)

    return selection


def emit_outputs(selection: SuiteSelection) -> None:
    lines = [f"{key}={value}" for key, value in selection.as_outputs().items()]
    for line in lines:
        print(line)

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output_file:
            for line in lines:
                output_file.write(f"{line}\n")


def main() -> int:
    if sys.stdin.isatty():
        print(
            "Error: This script expects a list of changed files via stdin.\n"
            "Usage example: git diff --name-only | python tools/select_test_suites.py",
            file=sys.stderr,
        )
        return 1

    selection = select_suites(
        sys.stdin,
        event_name=os.environ.get("GITHUB_EVENT_NAME"),
        ref_name=os.environ.get("GITHUB_REF_NAME") or os.environ.get("GITHUB_REF"),
    )
    emit_outputs(selection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
