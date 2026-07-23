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


def _full_backend_selection() -> SuiteSelection:
    return SuiteSelection(
        unit_fast=True,
        unit_slow=True,
        api_component=True,
        temporal_boundary=True,
        integration_ci=True,
        reliability_journey=True,
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

    return SuiteSelection(
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
        frontend_static=static or chromium,
        frontend_browser_chromium=chromium,
        frontend_browser_firefox=firefox,
        full_frontend=full_frontend,
    )


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
