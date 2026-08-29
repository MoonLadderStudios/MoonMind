"""Machine-enforced Omnigent package architecture.

Source issues: MoonLadderStudios/MoonMind#3701, MoonLadderStudios/MoonMind#3711.

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
        "moonmind/omnigent/harness_platform/harness_registry.py",
        "moonmind/omnigent/codex_execution_decisions.py",
        "moonmind/omnigent/execution_ports.py",
        "moonmind/omnigent/host_failures.py",
        "moonmind/omnigent/host_ports.py",
    ),
    "application": (
        "moonmind/omnigent/control_plane/turn_commands.py",
        "moonmind/omnigent/host_runtime.py",
        "moonmind/omnigent/realizers/base.py",
        "moonmind/omnigent/realizers/generic_host.py",
    ),
    "persistence": (
        "moonmind/omnigent/control_plane/repositories.py",
        "moonmind/omnigent/execution_adapters.py",
        "moonmind/omnigent/harness_platform/stores.py",
    ),
    "provider_host": (
        "moonmind/omnigent/bridge_artifacts.py",
        "moonmind/omnigent/bridge_store.py",
        "moonmind/omnigent/execute.py",
        "moonmind/omnigent/oauth_host_runtime.py",
        "moonmind/omnigent/profile_bound_execution.py",
        "moonmind/omnigent/workspace_publication.py",
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
    "moonmind.omnigent.profile_bound_execution",
)

FORBIDDEN_EVIDENCE_AUTHORITY_IMPORTS = (
    "moonmind.omnigent.harness_platform.planner",
)

# Container, process, and host-mutation authority. Only host adapters, the
# deployment qualification path, and the composition root may reach it.
CONTAINER_AND_PROCESS_MODULES = ("docker", "subprocess")
CONTAINER_AND_PROCESS_OWNERS = (
    "moonmind/omnigent/host_services/",
    "moonmind/omnigent/oauth_host_runtime.py",
    "moonmind/omnigent/bootstrap/qualification.py",
    "moonmind/omnigent/production.py",
)

# Deployment configuration is resolved at the composition/infrastructure
# boundary. Declared decision, coordination, persistence, UI, and evidence
# modules receive it as data.
CONFIGURATION_FREE_RESPONSIBILITIES = (
    "pure",
    "application",
    "persistence",
    "ui",
    "evidence_publication",
)
FORBIDDEN_CONFIGURATION_IMPORTS = (
    "moonmind.config",
    "moonmind.omnigent.settings",
)

# Provider-native vocabulary is normalized at adapter boundaries. Endpoint
# routes and provider runtime ids never leak into decisions, coordination,
# persistence, UI projections, or evidence.
PROVIDER_NATIVE_LITERALS = (
    "/v1/sessions",
    "/v1/harnesses",
    "/v1/hosts",
    "/v1/agents",
    "codex_cli",
    "claude_code",
)

OMNIGENT_ROUTERS = tuple(
    sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "api_service/api/routers").glob("omnigent*.py")
    )
)
FORBIDDEN_ROUTER_SIDE_EFFECT_IMPORTS = (
    "docker",
    "subprocess",
    "moonmind.omnigent.host_runtime",
    "moonmind.omnigent.oauth_host_runtime",
    "moonmind.omnigent.host_services",
    "moonmind.omnigent.credential_materializers",
    "moonmind.omnigent.harness_platform.materializers",
    "moonmind.omnigent.profile_bound_execution",
    "moonmind.omnigent.harness_platform.planner",
)

# Hermetic doubles and acceptance fixtures are test-owned. A production module
# that imports one has made a test artifact part of the deployed path.
TEST_DOUBLE_IMPORT_PREFIXES = ("tests", "tests.")

# Canonical session lifecycle code selects harnesses from persisted plan
# authority and the harness registration registry. A harness name appearing
# here means adding an approved harness would require a lifecycle edit.
SESSION_LIFECYCLE_MODULES = (
    "moonmind/workflows/temporal/activities/omnigent_session_activities.py",
    "moonmind/workflows/temporal/activities/omnigent_activities.py",
    "moonmind/workflows/temporal/workflows/omnigent_session.py",
    "moonmind/omnigent/host_runtime.py",
    "moonmind/omnigent/realizers/generic_host.py",
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



def _string_literals(relative_path: str) -> tuple[str, ...]:
    """Return executable string literals, excluding documentation."""

    tree = _tree(relative_path)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


def _reads_environment(relative_path: str) -> str | None:
    for node in ast.walk(_tree(relative_path)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr in {"getenv", "environ"}
        ):
            return f"os.{node.attr}"
    return None


def _declared_modules(*responsibilities: str) -> tuple[str, ...]:
    return tuple(
        path
        for responsibility in responsibilities
        for path in RESPONSIBILITY_MODULES[responsibility]
    )


def _exception_modules(rule: str) -> frozenset[str]:
    from moonmind.omnigent.legacy_retirement import (
        ARCHITECTURE_BOUNDARY_EXCEPTIONS,
    )

    return frozenset(
        item.module for item in ARCHITECTURE_BOUNDARY_EXCEPTIONS if item.rule == rule
    )


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


@pytest.mark.parametrize(
    "relative_path",
    sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "moonmind/omnigent").rglob("*.py")
    ),
)
def test_container_and_process_authority_stays_in_host_adapters(
    relative_path: str,
) -> None:
    leaked = [
        imported
        for imported in _imports(relative_path)
        if imported.split(".")[0] in CONTAINER_AND_PROCESS_MODULES
    ]
    if not leaked:
        return
    assert relative_path.startswith(CONTAINER_AND_PROCESS_OWNERS), (
        f"{relative_path} reaches container/process authority {leaked}; "
        "that belongs to a host adapter or the composition root"
    )


@pytest.mark.parametrize(
    "relative_path", _declared_modules(*CONFIGURATION_FREE_RESPONSIBILITIES)
)
def test_declared_layers_do_not_read_environment_or_settings(
    relative_path: str,
) -> None:
    read = _reads_environment(relative_path)
    assert read is None, (
        f"{relative_path} reads {read}; deployment configuration is resolved "
        "at the composition boundary and injected as data"
    )
    for imported in _imports(relative_path):
        assert not imported.startswith(FORBIDDEN_CONFIGURATION_IMPORTS), (
            f"{relative_path} imports deployment configuration {imported}"
        )


@pytest.mark.parametrize(
    "relative_path", _declared_modules(*CONFIGURATION_FREE_RESPONSIBILITIES)
)
def test_provider_native_vocabulary_is_normalized_at_adapters(
    relative_path: str,
) -> None:
    for literal in _string_literals(relative_path):
        for native in PROVIDER_NATIVE_LITERALS:
            assert native not in literal, (
                f"{relative_path} carries provider-native vocabulary {native!r}; "
                "normalize it in the adapter that speaks the provider protocol"
            )


@pytest.mark.parametrize("relative_path", OMNIGENT_ROUTERS)
def test_routers_have_no_provider_host_or_credential_side_effects(
    relative_path: str,
) -> None:
    allowed = _exception_modules("router_has_no_credential_or_host_lifecycle_import")
    leaked = [
        imported
        for imported in _imports(relative_path)
        if imported.startswith(FORBIDDEN_ROUTER_SIDE_EFFECT_IMPORTS)
    ]
    if leaked and relative_path in allowed:
        return
    assert not leaked, (
        f"{relative_path} performs provider, Docker, credential, or lifecycle "
        f"work via {leaked}; call one application or facade operation instead"
    )


def test_production_modules_do_not_import_test_doubles() -> None:
    offenders: list[str] = []
    for root in ("moonmind", "api_service"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            relative = str(path.relative_to(REPO_ROOT))
            for imported in _imports(relative):
                if imported.split(".")[0] in TEST_DOUBLE_IMPORT_PREFIXES:
                    offenders.append(f"{relative} -> {imported}")
    assert not offenders, (
        "production modules import test doubles or acceptance fixtures: "
        f"{offenders}"
    )


@pytest.mark.parametrize("relative_path", SESSION_LIFECYCLE_MODULES)
def test_canonical_lifecycle_code_has_no_harness_name_selection(
    relative_path: str,
) -> None:
    from moonmind.omnigent.harness_platform.harness_registry import (
        approved_harness_ids,
        canonical_harness_id,
    )

    names = set(approved_harness_ids())
    aliases = {
        alias
        for name in names
        for alias in (name.removesuffix("-native"),)
        if canonical_harness_id(alias) == name
    }
    for literal in _string_literals(relative_path):
        assert literal not in names | aliases, (
            f"{relative_path} selects on harness name {literal!r}; approved "
            "harnesses are registration data, not lifecycle branches"
        )


def test_every_architecture_exception_names_a_retirement_owner() -> None:
    from moonmind.omnigent.legacy_retirement import (
        ARCHITECTURE_BOUNDARY_EXCEPTIONS,
        assert_architecture_exceptions_are_owned,
    )

    assert_architecture_exceptions_are_owned()
    for exception in ARCHITECTURE_BOUNDARY_EXCEPTIONS:
        assert (REPO_ROOT / exception.module).is_file(), (
            f"architecture exception {exception.exception_id} names a module "
            "that no longer exists; delete the exemption with the module"
        )


@pytest.mark.parametrize(
    "source",
    (
        "import docker",
        "import subprocess",
        "from subprocess import run",
    ),
)
def test_container_and_process_leakage_negative_fixtures(source: str) -> None:
    leaked = [
        imported
        for imported in _imports_from_tree(ast.parse(source))
        if imported.split(".")[0] in CONTAINER_AND_PROCESS_MODULES
    ]
    assert leaked


@pytest.mark.parametrize(
    "source",
    (
        "PATH = '/v1/sessions/{session_id}'",
        "RUNTIME = 'codex_cli'",
        "RUNTIME = 'claude_code'",
    ),
)
def test_provider_native_vocabulary_negative_fixtures(source: str) -> None:
    literals = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert any(
        native in literal
        for literal in literals
        for native in PROVIDER_NATIVE_LITERALS
    )


@pytest.mark.parametrize(
    "source",
    (
        "OWNER = os.environ['OMNIGENT_HOST_IMAGE_REF']",
        "OWNER = os.getenv('OMNIGENT_SERVER_URL')",
    ),
)
def test_environment_read_negative_fixtures(source: str, tmp_path) -> None:
    fixture = tmp_path / "fixture.py"
    fixture.write_text(f"import os\n{source}\n", encoding="utf-8")
    tree = ast.parse(fixture.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr in {"getenv", "environ"}
        for node in ast.walk(tree)
    )
