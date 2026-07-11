#!/usr/bin/env python3
"""Select backend CI test suites from changed paths.

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
    "api_component",
    "temporal_boundary",
    "integration_ci",
    "reliability_journey",
    "full_backend",
)

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

FORCE_FULL_PREFIXES = (
    ".github/workflows/",
)

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

API_COMPONENT_GLOBS = (
    "api_service/auth*",
)

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

RELIABILITY_JOURNEY_EXACT = {
    "api_service/Dockerfile",
    "docker-compose.test.yaml",
    "moonmind/schemas/agent_runtime_models.py",
    "moonmind/schemas/managed_session_models.py",
    "tests/helpers/codex_session_runtime.py",
}

RELIABILITY_JOURNEY_PREFIXES = (
    ".agents/skills/",
    "api_service/docker/",
    "docker/",
    "moonmind/workflows/adapters/",
    "moonmind/workflows/temporal/",
    "tests/reliability/",
)

RELIABILITY_JOURNEY_GLOBS = (
    "moonmind/schemas/*checkpoint*",
)

BACKEND_PREFIXES = (
    ".agents/skills/",
    "api_service/",
    "docker/",
    "moonmind/",
    "tests/unit/",
    "tests/integration/",
    "tests/reliability/",
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
    api_component: bool = False
    temporal_boundary: bool = False
    integration_ci: bool = False
    reliability_journey: bool = False
    full_backend: bool = False

    def as_outputs(self) -> dict[str, str]:
        return {
            key: "true" if getattr(self, key) else "false" for key in OUTPUT_KEYS
        }


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
        api_component=True,
        temporal_boundary=True,
        integration_ci=True,
        reliability_journey=True,
        full_backend=True,
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
        return _full_backend_selection()
    if not paths:
        return _full_backend_selection()
    if any(
        _matches(path, exact=FORCE_FULL_EXACT, prefixes=FORCE_FULL_PREFIXES)
        for path in paths
    ):
        return _full_backend_selection()

    unknown_paths = [
        path
        for path in paths
        if not _is_backend_path(path) and not _is_non_backend_path(path)
    ]
    if unknown_paths:
        return _full_backend_selection()

    backend_paths = [path for path in paths if _is_backend_path(path)]
    if not backend_paths:
        return SuiteSelection()

    return SuiteSelection(
        unit_fast=True,
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
