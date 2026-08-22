"""Machine-enforced Omnigent package architecture.

Source issue: MoonLadderStudios/MoonMind#3701.

The contract mirrors the responsibility table in
``docs/Omnigent/OmnigentHarnessPlatformDesign.md``. Keep the rules here
data-driven: adding a concrete module to a responsibility makes the one-way
dependency checks apply to it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]

RESPONSIBILITY_MODULES: dict[str, tuple[str, ...]] = {
    "pure": (
        "moonmind/omnigent/reconciler/contracts.py",
        "moonmind/omnigent/reconciler/reducer.py",
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
    ),
    "application": (
        "moonmind/omnigent/control_plane/turn_commands.py",
        "moonmind/omnigent/realizers/base.py",
        "moonmind/omnigent/realizers/generic_host.py",
    ),
    "persistence": (
        "moonmind/omnigent/control_plane/repositories.py",
        "moonmind/omnigent/harness_platform/stores.py",
    ),
    "provider_host": (
        "moonmind/omnigent/bridge_artifacts.py",
        "moonmind/omnigent/bridge_store.py",
        "moonmind/omnigent/execute.py",
        "moonmind/omnigent/host_runtime.py",
        "moonmind/omnigent/oauth_host_runtime.py",
    ),
    "workspace_credential": (
        "moonmind/omnigent/repository_sources.py",
        "moonmind/omnigent/workspace_intent.py",
        "moonmind/omnigent/harness_platform/materializers.py",
    ),
    "composition": (
        "api_service/api/routers/omnigent_catalog.py",
        "moonmind/workflows/temporal/activities/omnigent_activities.py",
        "moonmind/workflows/temporal/activities/omnigent_session_activities.py",
    ),
    "ui": (
        "moonmind/omnigent/workflow_chat_facade.py",
        "api_service/api/routers/omnigent_session_timeline.py",
    ),
    "evidence_publication": (
        "moonmind/omnigent/conformance.py",
        "moonmind/omnigent/exact_artifact_conformance.py",
        "moonmind/omnigent/workflow_chat_acceptance.py",
        "moonmind/omnigent/control_plane/timeline.py",
    ),
}

# Same-layer dependencies are always permitted. Cross-layer entries are the
# complete inward-facing dependency graph. In particular, UI and evidence do
# not own persistence, host, credential, plan, or session authority.
ALLOWED_DEPENDENCIES: dict[str, frozenset[str]] = {
    "pure": frozenset({"pure"}),
    "application": frozenset({"pure", "application"}),
    "persistence": frozenset({"pure", "persistence"}),
    "provider_host": frozenset(
        {
            "pure",
            "application",
            "persistence",
            "workspace_credential",
            "provider_host",
        }
    ),
    "workspace_credential": frozenset({"pure", "workspace_credential"}),
    "composition": frozenset(RESPONSIBILITY_MODULES),
    "ui": frozenset({"pure", "application", "ui", "evidence_publication"}),
    "evidence_publication": frozenset(
        {"pure", "application", "evidence_publication"}
    ),
}

FORBIDDEN_DOMAIN_INFRASTRUCTURE_IMPORTS = (
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

FORBIDDEN_UI_AUTHORITY_IMPORTS = (
    "moonmind.omnigent.control_plane.repositories",
    "moonmind.omnigent.harness_platform.materializers",
    "moonmind.omnigent.harness_platform.stores",
    "moonmind.omnigent.host_runtime",
    "moonmind.omnigent.oauth_host_runtime",
)

FORBIDDEN_EVIDENCE_AUTHORITY_IMPORTS = (
    "moonmind.omnigent.harness_platform.planner",
)


def _tree(relative_path: str) -> ast.AST:
    return ast.parse((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def _imports_from_tree(
    tree: ast.AST, *, current_module: str = "fixture"
) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level:
                package = current_module.split(".")[: -node.level]
                imported.append(".".join([*package, node.module]))
            else:
                imported.append(node.module)
    return tuple(imported)


def _imports(relative_path: str) -> tuple[str, ...]:
    current_module = relative_path.removesuffix(".py").replace("/", ".")
    return _imports_from_tree(_tree(relative_path), current_module=current_module)


def _assert_dependency_allowed(owner: str, dependency: str) -> None:
    if dependency not in ALLOWED_DEPENDENCIES[owner]:
        raise AssertionError(
            f"Omnigent {owner} responsibility may not depend on {dependency}"
        )


def _assert_no_import_prefixes(source: str, prefixes: tuple[str, ...]) -> None:
    for imported in _imports_from_tree(ast.parse(source)):
        if imported.startswith(prefixes):
            raise AssertionError(f"forbidden infrastructure import: {imported}")


def test_every_documented_responsibility_has_machine_enforced_modules() -> None:
    assert set(RESPONSIBILITY_MODULES) == {
        "pure",
        "application",
        "persistence",
        "provider_host",
        "workspace_credential",
        "composition",
        "ui",
        "evidence_publication",
    }
    assert all(RESPONSIBILITY_MODULES.values())
    for paths in RESPONSIBILITY_MODULES.values():
        assert all((REPO_ROOT / path).is_file() for path in paths)


@pytest.mark.parametrize(
    "relative_path",
    RESPONSIBILITY_MODULES["pure"] + RESPONSIBILITY_MODULES["application"],
)
def test_domain_and_application_modules_do_not_import_infrastructure(
    relative_path: str,
) -> None:
    for imported in _imports(relative_path):
        assert not imported.startswith(FORBIDDEN_DOMAIN_INFRASTRUCTURE_IMPORTS), (
            f"{relative_path} imports outer infrastructure {imported}; "
            "inject it at the composition boundary"
        )


def test_declared_responsibility_dependency_graph_is_one_way_and_acyclic() -> None:
    module_to_layer = {
        path.removesuffix(".py").replace("/", "."): layer
        for layer, paths in RESPONSIBILITY_MODULES.items()
        for path in paths
    }
    for owner_layer, paths in RESPONSIBILITY_MODULES.items():
        for path in paths:
            for imported in _imports(path):
                dependency_layer = module_to_layer.get(imported)
                if dependency_layer is not None:
                    _assert_dependency_allowed(owner_layer, dependency_layer)

    # A cycle between responsibilities would require mutual permission. Same
    # layer edges are intentionally ignored because they share one owner.
    edges = {
        owner: set(allowed) - {owner}
        for owner, allowed in ALLOWED_DEPENDENCIES.items()
    }
    for owner, dependencies in edges.items():
        for dependency in dependencies:
            assert owner not in edges[dependency], (
                f"responsibility cycle permitted between {owner} and {dependency}"
            )


@pytest.mark.parametrize("relative_path", RESPONSIBILITY_MODULES["ui"])
def test_ui_facades_cannot_import_authority_owning_adapters(relative_path: str) -> None:
    for imported in _imports(relative_path):
        assert not imported.startswith(FORBIDDEN_UI_AUTHORITY_IMPORTS), (
            f"{relative_path} bypasses application projections via {imported}"
        )


@pytest.mark.parametrize(
    "relative_path", RESPONSIBILITY_MODULES["evidence_publication"]
)
def test_evidence_and_publication_cannot_replace_execution_authority(
    relative_path: str,
) -> None:
    for imported in _imports(relative_path):
        assert not imported.startswith(FORBIDDEN_EVIDENCE_AUTHORITY_IMPORTS), (
            f"{relative_path} imports authority-producing module {imported}"
        )


def test_generic_realizer_has_no_harness_specific_selection_branch() -> None:
    source = (REPO_ROOT / "moonmind/omnigent/realizers/generic_host.py").read_text(
        encoding="utf-8"
    )
    for harness_id in ("codex-native", "opencode-native", "pi-native"):
        assert harness_id not in source


def test_canonical_authority_identity_functions_have_one_owner() -> None:
    names = {
        "canonical_omnigent_session_id",
        "canonical_omnigent_turn_attempt_id",
        "omnigent_session_workflow_id",
        "canonical_turn_command_key",
        "canonical_turn_claim_token",
        "canonical_followup_turn_attempt_id",
        "canonical_turn_command_id",
    }
    definitions: dict[str, list[str]] = {name: [] for name in names}
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


def test_canonical_identity_vocabulary_is_not_reimplemented() -> None:
    forbidden_literals = (
        "omnigent-session/v1",
        "omnigent-turn/v1",
        "omnigent-turn-command:",
    )
    owner = REPO_ROOT / "moonmind/omnigent/control_plane/identities.py"
    for root in (REPO_ROOT / "moonmind/omnigent", REPO_ROOT / "api_service"):
        for path in root.rglob("*.py"):
            if path == owner:
                continue
            source = path.read_text(encoding="utf-8")
            for literal in forbidden_literals:
                assert literal not in source, (
                    f"{path.relative_to(REPO_ROOT)} reimplements canonical "
                    f"authority vocabulary {literal!r}"
                )


def test_generic_realizer_does_not_import_api_persistence() -> None:
    realizer_files = (REPO_ROOT / "moonmind/omnigent/realizers").glob("*.py")
    importing_api = {
        path.name
        for path in realizer_files
        if any(
            name.startswith("api_service")
            for name in _imports(str(path.relative_to(REPO_ROOT)))
        )
    }
    # Codex remains an explicit legacy realizer. Generic infrastructure is
    # assembled only by the outer production composition module.
    assert importing_api == {"codex_profile_bound.py", "registry.py"}


@pytest.mark.parametrize(
    ("owner", "dependency"),
    (
        ("pure", "application"),
        ("application", "persistence"),
        ("persistence", "provider_host"),
        ("provider_host", "ui"),
        ("workspace_credential", "ui"),
        ("ui", "persistence"),
        ("ui", "provider_host"),
        ("evidence_publication", "composition"),
    ),
)
def test_forbidden_dependency_directions_have_negative_fixtures(
    owner: str, dependency: str
) -> None:
    with pytest.raises(AssertionError, match="may not depend"):
        _assert_dependency_allowed(owner, dependency)


@pytest.mark.parametrize(
    "source",
    (
        "import sqlalchemy",
        "from fastapi import APIRouter",
        "import temporalio",
        "from moonmind.workflows.temporal.client import TemporalClientAdapter",
        "import subprocess",
    ),
)
def test_framework_leakage_negative_fixtures(source: str) -> None:
    with pytest.raises(AssertionError, match="forbidden infrastructure import"):
        _assert_no_import_prefixes(source, FORBIDDEN_DOMAIN_INFRASTRUCTURE_IMPORTS)


@pytest.mark.parametrize(
    "source",
    (
        "from moonmind.omnigent.host_runtime import GenericOmnigentHostRuntime",
        "from moonmind.omnigent.harness_platform.materializers import get_materializer",
        "from moonmind.omnigent.control_plane.repositories import ControlPlaneRepositories",
    ),
)
def test_ui_authority_bypass_negative_fixtures(source: str) -> None:
    with pytest.raises(AssertionError, match="forbidden infrastructure import"):
        _assert_no_import_prefixes(source, FORBIDDEN_UI_AUTHORITY_IMPORTS)


@pytest.mark.parametrize(
    "source",
    (
        'SESSION_KIND = "omnigent-session/v1"',
        'TURN_KIND = "omnigent-turn/v1"',
        'COMMAND_KEY = "omnigent-turn-command:" + key',
    ),
)
def test_duplicate_authority_vocabulary_negative_fixtures(source: str) -> None:
    assert any(
        literal in source
        for literal in (
            "omnigent-session/v1",
            "omnigent-turn/v1",
            "omnigent-turn-command:",
        )
    )
