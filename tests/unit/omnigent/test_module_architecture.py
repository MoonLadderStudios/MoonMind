"""Machine-enforced Omnigent package architecture.

Source issue: MoonLadderStudios/MoonMind#3701.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]

PURE_MODULES = (
    "moonmind/omnigent/control_plane/records.py",
    "moonmind/omnigent/control_plane/identities.py",
    "moonmind/omnigent/harness_platform/agent_profile.py",
    "moonmind/omnigent/harness_platform/attestation.py",
    "moonmind/omnigent/harness_platform/capabilities.py",
    "moonmind/omnigent/harness_platform/catalog.py",
    "moonmind/omnigent/harness_platform/credential_bindings.py",
    "moonmind/omnigent/harness_platform/execution_plan.py",
    "moonmind/omnigent/harness_platform/failures.py",
    "moonmind/omnigent/harness_platform/runtime_binding.py",
    "moonmind/omnigent/harness_platform/skills.py",
    "moonmind/omnigent/harness_platform/support.py",
)

APPLICATION_MODULES = (
    "moonmind/omnigent/control_plane/turn_commands.py",
    "moonmind/omnigent/realizers/base.py",
    "moonmind/omnigent/realizers/generic_host.py",
)

FORBIDDEN_INFRASTRUCTURE_IMPORTS = (
    "api_service",
    "sqlalchemy",
    "fastapi",
    "temporalio",
    "httpx",
    "requests",
    "aiohttp",
    "docker",
    "os",
    "pathlib",
    "shutil",
    "socket",
    "subprocess",
    "moonmind.config",
    "moonmind.workflows",
    "moonmind.repositories",
)


def _tree(relative_path: str) -> ast.AST:
    return ast.parse((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def _imports(relative_path: str) -> tuple[str, ...]:
    imported: list[str] = []
    current_module = relative_path.removesuffix(".py").replace("/", ".")
    for node in ast.walk(_tree(relative_path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level:
                package = current_module.split(".")[: -node.level]
                imported.append(".".join([*package, node.module]))
            else:
                imported.append(node.module)
    return tuple(imported)


@pytest.mark.parametrize("relative_path", PURE_MODULES + APPLICATION_MODULES)
def test_domain_and_application_modules_do_not_import_infrastructure(
    relative_path: str,
) -> None:
    for imported in _imports(relative_path):
        assert not imported.startswith(FORBIDDEN_INFRASTRUCTURE_IMPORTS), (
            f"{relative_path} imports outer infrastructure {imported}; "
            "inject it at the composition boundary"
        )


def test_generic_realizer_has_no_harness_specific_selection_branch() -> None:
    source = (REPO_ROOT / "moonmind/omnigent/realizers/generic_host.py").read_text(
        encoding="utf-8"
    )
    for harness_id in ("codex-native", "opencode-native", "pi-native"):
        assert harness_id not in source


def test_domain_and_application_dependency_graph_is_acyclic() -> None:
    paths = PURE_MODULES + APPLICATION_MODULES
    module_by_path = {
        path: path.removesuffix(".py").replace("/", ".") for path in paths
    }
    path_by_module = {module: path for path, module in module_by_path.items()}
    edges = {
        path: {
            path_by_module[imported]
            for imported in _imports(path)
            if imported in path_by_module
        }
        for path in paths
    }
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(path: str) -> None:
        if path in visiting:
            cycle = visiting[visiting.index(path) :] + [path]
            pytest.fail("Omnigent package dependency cycle: " + " -> ".join(cycle))
        if path in visited:
            return
        visiting.append(path)
        for dependency in edges[path]:
            visit(dependency)
        visiting.pop()
        visited.add(path)

    for path in paths:
        visit(path)


def test_canonical_authority_identity_functions_have_one_owner() -> None:
    definitions: dict[str, list[str]] = {
        "canonical_omnigent_session_id": [],
        "canonical_omnigent_turn_attempt_id": [],
        "omnigent_session_workflow_id": [],
    }
    roots = (REPO_ROOT / "moonmind/omnigent", REPO_ROOT / "api_service")
    for root in roots:
        for path in root.rglob("*.py"):
            relative = str(path.relative_to(REPO_ROOT))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in definitions:
                        definitions[node.name].append(relative)
    expected = "moonmind/omnigent/control_plane/identities.py"
    assert definitions == {name: [expected] for name in definitions}


def test_composition_is_the_only_generic_realizer_api_persistence_import() -> None:
    realizer_files = (REPO_ROOT / "moonmind/omnigent/realizers").glob("*.py")
    importing_api = {
        path.name
        for path in realizer_files
        if any(
            name.startswith("api_service")
            for name in _imports(str(path.relative_to(REPO_ROOT)))
        )
    }
    # Codex remains an explicit legacy realizer. Its eventual retirement is
    # governed by the existing cutover gate; new generic infrastructure belongs
    # only in composition.py.
    assert importing_api == {
        "codex_profile_bound.py",
        "composition.py",
        "runtime_authority.py",
    }
