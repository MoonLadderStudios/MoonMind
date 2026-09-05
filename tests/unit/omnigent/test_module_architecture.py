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
        "moonmind/omnigent/host_auth_contracts.py",
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
        "api_service/api/routers/omnigent_bridge_composition.py",
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

# A raw container-runtime argument vector is container authority even when it
# is shelled out instead of imported. Building one belongs to a host adapter;
# an Omnigent module that assembles its own ``docker`` command has taken
# authority that ``host_services/`` owns.
CONTAINER_COMMAND_LITERALS = ("docker", "docker-compose")
CONTAINER_COMMAND_OWNERS = (
    "moonmind/omnigent/host_services/",
    "moonmind/omnigent/bootstrap/",
    "moonmind/omnigent/credential_materializers.py",
    "moonmind/omnigent/opencode_runtime_validation.py",
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

_ALL_OMNIGENT_ROUTER_MODULES = tuple(
    sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "api_service/api/routers").glob("omnigent*.py")
    )
)

# The composition root is the one module under ``routers/`` whose job is to name
# concrete stores, transports, facades, and credential profiles. Excluding it
# from the route-handler rules is what makes those rules meaningful: every other
# omnigent router must reach that capability through this module.
OMNIGENT_ROUTER_COMPOSITION_MODULES = (
    "api_service/api/routers/omnigent_bridge_composition.py",
)
OMNIGENT_ROUTERS = tuple(
    path
    for path in _ALL_OMNIGENT_ROUTER_MODULES
    if path not in OMNIGENT_ROUTER_COMPOSITION_MODULES
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
    "moonmind.omnigent.host_auth_profile",
    "moonmind.omnigent.host_auth_store",
)

# Concrete persistence, credential, and durable-session constructors. A route
# handler may *name* these types — annotations, dependency signatures, and the
# error vocabulary it maps to HTTP are route contract — but calling them binds
# the handler to one backing implementation and puts a database session, a
# SecretRef read, or a lifecycle transition inside route scope. Deciding which
# implementation backs a call is composition.
FORBIDDEN_ROUTER_SIDE_EFFECT_CALLS = (
    "OmnigentBridgeSessionStore",
    "OmnigentEmbeddedHostProtocolFacade",
    "HostAuthProfileStore",
    "TemporalArtifactRepository",
    "TemporalArtifactService",
    "async_session_maker",
    "host_auth_readiness",
    "load_host_auth_profile",
    "resolve_host_auth_credentials",
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


def _called_names_from_tree(tree: ast.AST) -> tuple[str, ...]:
    """Return every simple or attribute callee name invoked in a tree.

    Annotations, ``Depends`` signatures, and ``except`` clauses name a type
    without calling it, so they never appear here. That is the discrimination
    the router rule needs: naming a store type is route contract, building one
    is composition.
    """

    called: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.append(func.id)
        elif isinstance(func, ast.Attribute):
            called.append(func.attr)
    return tuple(called)


def _called_names(relative_path: str) -> tuple[str, ...]:
    return _called_names_from_tree(_tree(relative_path))


def _container_command_literals_from_tree(tree: ast.AST) -> tuple[str, ...]:
    """Return container-runtime executables invoked as a call argument."""

    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # A command is passed either as leading varargs or as one argv sequence.
        candidates: list[ast.expr] = list(node.args)
        for argument in node.args:
            if isinstance(argument, (ast.List, ast.Tuple)):
                candidates.extend(argument.elts)
        for argument in candidates:
            if (
                isinstance(argument, ast.Constant)
                and argument.value in CONTAINER_COMMAND_LITERALS
            ):
                found.append(argument.value)
    return tuple(found)


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


def _omnigent_modules() -> tuple[str, ...]:
    return tuple(
        sorted(
            str(path.relative_to(REPO_ROOT))
            for path in (REPO_ROOT / "moonmind/omnigent").rglob("*.py")
        )
    )


@pytest.mark.parametrize("relative_path", _omnigent_modules())
def test_raw_container_commands_belong_to_host_adapters(relative_path: str) -> None:
    if not _container_command_literals_from_tree(_tree(relative_path)):
        return
    if relative_path.startswith(CONTAINER_COMMAND_OWNERS):
        return
    allowed = _exception_modules("adapter_issues_no_raw_container_command")
    assert relative_path in allowed, (
        f"{relative_path} assembles a container-runtime command itself; move it "
        "behind a host_services adapter, or register a bounded exemption that "
        "names the #3712 retirement path owning its removal"
    )


@pytest.mark.parametrize(
    "source",
    (
        'await self._run("docker", "rm", "-f", name, check=False)',
        'run(["docker-compose", "up"])',
    ),
)
def test_raw_container_command_negative_fixtures(source: str) -> None:
    assert _container_command_literals_from_tree(ast.parse(source))


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


@pytest.mark.parametrize("relative_path", OMNIGENT_ROUTERS)
def test_routers_do_not_construct_persistence_or_credential_adapters(
    relative_path: str,
) -> None:
    """A route handler may name these types; only composition may build them."""

    allowed = _exception_modules("router_builds_no_persistence_or_credential_adapter")
    constructed = sorted(
        {
            name
            for name in _called_names(relative_path)
            if name in FORBIDDEN_ROUTER_SIDE_EFFECT_CALLS
        }
    )
    if constructed and relative_path in allowed:
        return
    assert not constructed, (
        f"{relative_path} constructs or invokes {constructed} in route scope; "
        "select the backing implementation in "
        f"{OMNIGENT_ROUTER_COMPOSITION_MODULES[0]} and call one operation"
    )


def test_router_composition_root_is_declared_and_singular() -> None:
    """The exclusion list may not quietly grow into a second router layer."""

    for path in OMNIGENT_ROUTER_COMPOSITION_MODULES:
        assert (REPO_ROOT / path).is_file()
        assert path in _ALL_OMNIGENT_ROUTER_MODULES
    assert set(OMNIGENT_ROUTER_COMPOSITION_MODULES) <= set(
        RESPONSIBILITY_MODULES["composition"]
    )
    # Every other omnigent router is held to the route-handler rules.
    assert set(OMNIGENT_ROUTERS) == set(_ALL_OMNIGENT_ROUTER_MODULES) - set(
        OMNIGENT_ROUTER_COMPOSITION_MODULES
    )
    assert not any(
        path.endswith("_composition.py") for path in OMNIGENT_ROUTERS
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
        "from moonmind.omnigent.host_auth_profile import resolve_host_auth_credentials",
        "from moonmind.omnigent.host_auth_store import HostAuthProfileStore",
        "from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime",
    ),
)
def test_router_side_effect_import_negative_fixtures(source: str) -> None:
    leaked = [
        imported
        for imported in _imports_from_tree(ast.parse(source))
        if imported.startswith(FORBIDDEN_ROUTER_SIDE_EFFECT_IMPORTS)
    ]
    assert leaked


@pytest.mark.parametrize(
    "source",
    (
        "store = OmnigentBridgeSessionStore(async_session_maker)",
        "facade = OmnigentEmbeddedHostProtocolFacade(run_store=store, config=config)",
        "profile = await resolve_host_auth_credentials(profile=candidate)",
        "auth = await host_auth_readiness(profile=profile)",
        "durable = HostAuthProfileStore(async_session_maker)",
        "service = TemporalArtifactService(TemporalArtifactRepository(session))",
    ),
)
def test_router_adapter_construction_negative_fixtures(source: str) -> None:
    """Each fixture is a route-scope construction the rule must reject."""

    constructed = [
        name
        for name in _called_names_from_tree(ast.parse(source))
        if name in FORBIDDEN_ROUTER_SIDE_EFFECT_CALLS
    ]
    assert constructed


@pytest.mark.parametrize(
    "source",
    (
        "def handler(store: OmnigentBridgeSessionStore = Depends(_get_store)): ...",
        "def project(store: OmnigentBridgeSessionStore) -> dict: ...",
        "async def handler(facade: OmnigentEmbeddedHostProtocolFacade | None): ...",
    ),
)
def test_router_may_name_adapter_types_without_constructing_them(source: str) -> None:
    """Naming the type in a signature is route contract, not composition."""

    constructed = [
        name
        for name in _called_names_from_tree(ast.parse(source))
        if name in FORBIDDEN_ROUTER_SIDE_EFFECT_CALLS
    ]
    assert not constructed


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


# ---------------------------------------------------------------------------
# Permanent retirement guards (MoonLadderStudios/MoonMind#3835 required work 11)
#
# After retirement, CI must reject reintroduction of the duplicate architecture.
# Each rule below is derived from the code-owned retirement inventory rather
# than a hand-maintained list, so a *new* duplicate path fails these tests until
# it is either removed or explicitly classified with a retirement class.
# ---------------------------------------------------------------------------

# The one module allowed to hold the versioned direct-vs-generic rollout
# decision, and the one module allowed to hold the deployment runtime default.
VERSIONED_ROLLOUT_AUTHORITY_MODULES = (
    "moonmind/omnigent/cutover.py",
    "moonmind/workflows/executions/runtime_defaults.py",
)

# Credential materialization handles are registered in exactly these modules.
# A new credential path anywhere else bypasses runtime-pack and materializer
# registration.
CREDENTIAL_REGISTRATION_MODULES = (
    "moonmind/omnigent/harness_platform/materializers.py",
    "moonmind/omnigent/harness_platform/runtime_packs.py",
    "moonmind/omnigent/credential_materializers.py",
)
_MATERIALIZER_REF_PATTERN = __import__("re").compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*-(?:oauth-home|auth-json|api-key)@\d+$"
)

# Shared-image resolution has one authority. A provider-specific resolver would
# reintroduce per-provider image identity behind the shared image.
SHARED_IMAGE_AUTHORITY_MODULES = (
    "moonmind/omnigent/harness_platform/host_classes.py",
    "moonmind/omnigent/harness_platform/static_hosts.py",
    "moonmind/omnigent/bootstrap/image_resolution.py",
    "moonmind/omnigent/bootstrap/store.py",
)
SHARED_IMAGE_ENV = "OMNIGENT_SHARED_HOST_IMAGE_REF"

# Compose topology is one file plus the test-only project file.
ALLOWED_COMPOSE_FILES = ("docker-compose.yaml", "docker-compose.test.yaml")

_PROVIDER_LIFECYCLE_SUFFIXES = ("Coordinator", "Runtime", "Lifecycle", "Launcher")
_PROVIDER_NAMES = ("Codex", "Claude", "OpenCode")


def _inventory_surfaces() -> frozenset[str]:
    from moonmind.omnigent.legacy_retirement import RETIREMENT_INVENTORY

    return frozenset(ref for path in RETIREMENT_INVENTORY for ref in path.surfaces)


def _provider_lifecycle_classes(tree: ast.AST) -> tuple[str, ...]:
    """Top-level classes that own a provider-named lifecycle."""

    return tuple(
        node.name
        for node in getattr(tree, "body", [])
        if isinstance(node, ast.ClassDef)
        and node.name.endswith(_PROVIDER_LIFECYCLE_SUFFIXES)
        and any(provider in node.name for provider in _PROVIDER_NAMES)
    )


def _direct_default_assignments(
    tree: ast.AST, direct_ids: frozenset[str]
) -> tuple[str, ...]:
    """Module-level ``*DEFAULT*`` constants bound to a direct runtime id."""

    found: list[str] = []
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not any("DEFAULT" in name for name in names):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and value.value in direct_ids:
            found.append(names[0])
    return tuple(found)


def _legacy_fallback_constants(
    tree: ast.AST, legacy_values: frozenset[str]
) -> tuple[str, ...]:
    """Legacy realizer/runtime literals produced inside an exception handler."""

    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and inner.value in legacy_values:
                found.append(str(inner.value))
    return tuple(found)


def test_no_new_top_level_provider_lifecycle_coordinator() -> None:
    """A provider-named lifecycle owner must be an inventoried legacy path."""

    surfaces = _inventory_surfaces()
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "moonmind/omnigent").rglob("*.py")):
        relative = str(path.relative_to(REPO_ROOT))
        module = relative.removesuffix(".py").replace("/", ".")
        for name in _provider_lifecycle_classes(
            ast.parse(path.read_text(encoding="utf-8"))
        ):
            if f"python:{module}:{name}" in surfaces:
                continue
            offenders.append(f"{relative}:{name}")
    assert not offenders, (
        "new top-level provider lifecycle coordinators must not be introduced; "
        "the generic host owns the lifecycle and every retained provider "
        f"coordinator carries a retirement row: {offenders}"
    )


def test_direct_default_selection_stays_in_versioned_rollout_authority() -> None:
    """Only the rollout authority may name a direct runtime as a default."""

    from moonmind.workflows.temporal.runtime.strategies import RUNTIME_STRATEGIES

    direct_ids = frozenset(RUNTIME_STRATEGIES)
    offenders: list[str] = []
    for root in ("moonmind", "api_service"):
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            relative = str(path.relative_to(REPO_ROOT))
            if relative in VERSIONED_ROLLOUT_AUTHORITY_MODULES:
                continue
            for name in _direct_default_assignments(
                ast.parse(path.read_text(encoding="utf-8")), direct_ids
            ):
                offenders.append(f"{relative}:{name}")
    assert not offenders, (
        "a direct runtime may only become a default through the versioned "
        f"rollout authority {VERSIONED_ROLLOUT_AUTHORITY_MODULES}: {offenders}"
    )


def test_no_implicit_fallback_from_generic_omnigent() -> None:
    """A generic failure may never substitute a legacy realizer or runtime."""

    from moonmind.omnigent.harness_platform.support import KNOWN_REALIZERS
    from moonmind.omnigent.retirement_surfaces import GENERIC_REALIZER_REF
    from moonmind.workflows.temporal.runtime.strategies import RUNTIME_STRATEGIES

    legacy_values = frozenset(
        {ref for ref in KNOWN_REALIZERS if ref != GENERIC_REALIZER_REF}
        | set(RUNTIME_STRATEGIES)
    )
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "moonmind/omnigent").rglob("*.py")):
        relative = str(path.relative_to(REPO_ROOT))
        for value in _legacy_fallback_constants(
            ast.parse(path.read_text(encoding="utf-8")), legacy_values
        ):
            offenders.append(f"{relative}: {value}")
    assert not offenders, (
        "an except handler that yields a legacy realizer or direct runtime is "
        f"an implicit fallback from generic Omnigent: {offenders}"
    )


def test_provider_profile_capacity_ownership_is_singular() -> None:
    names = {
        "acquire_provider_lease",
        "release_provider_lease",
        "evaluate_generic_host_capacity",
    }
    definitions: dict[str, list[str]] = {name: [] for name in names}
    for root in (REPO_ROOT / "moonmind/omnigent", REPO_ROOT / "api_service"):
        for path in sorted(root.rglob("*.py")):
            relative = str(path.relative_to(REPO_ROOT))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in definitions:
                        definitions[node.name].append(relative)
    for name, owners in definitions.items():
        assert len(owners) <= 1, (
            f"Provider Profile capacity function {name!r} has duplicate owners: "
            f"{owners}"
        )


def _reads_environment_key(tree: ast.AST, key: str) -> bool:
    """Whether a module resolves ``key`` from the environment.

    Naming the key (as documentation, a replacement hint, or a value passed into
    a launched host) is not resolution. Reading it to decide an image is.
    """

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            index = node.slice
            if isinstance(index, ast.Constant) and index.value == key:
                return True
        elif isinstance(node, ast.Call):
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            name = func.id if isinstance(func, ast.Name) else None
            if attr in {"getenv", "get"} or (name and "image_ref" in name):
                if any(
                    isinstance(arg, ast.Constant) and arg.value == key
                    for arg in node.args
                ):
                    return True
    return False


def test_shared_image_resolution_is_not_provider_specific() -> None:
    """Only the image authority may resolve the shared host image.

    A provider-specific resolver would reintroduce per-provider image identity
    behind the one shared image. A module that still resolves it because it owns
    a retained legacy path is exempt only while it carries a retirement row, so
    the exemption disappears with the code.
    """

    surfaces = _inventory_surfaces()
    offenders: list[str] = []
    for root in ("moonmind", "api_service"):
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            relative = str(path.relative_to(REPO_ROOT))
            if relative in SHARED_IMAGE_AUTHORITY_MODULES:
                continue
            module = relative.removesuffix(".py").replace("/", ".")
            if any(ref.startswith(f"python:{module}:") for ref in surfaces):
                continue
            if _reads_environment_key(
                ast.parse(path.read_text(encoding="utf-8")), SHARED_IMAGE_ENV
            ):
                offenders.append(relative)
    assert not offenders, (
        "shared host image resolution has one authority "
        f"{SHARED_IMAGE_AUTHORITY_MODULES}: {offenders}"
    )


def test_every_provider_specific_image_variable_is_inventoried() -> None:
    """A per-provider image identity must carry a retirement class."""

    from moonmind.omnigent.legacy_retirement import assert_inventory_is_complete
    from moonmind.omnigent.retirement_surfaces import (
        declared_image_environment_variables,
    )

    surfaces = _inventory_surfaces()
    for variable in declared_image_environment_variables():
        assert f"env:{variable}" in surfaces, (
            f"per-provider image variable {variable} has no retirement row"
        )
    assert_inventory_is_complete()


def test_credential_paths_stay_in_runtime_pack_and_materializer_registration() -> None:
    """A credential handle must come from the materializer registry.

    Consuming a registered materializer ref is ordinary Host Class, runtime-pack,
    and conformance work. Naming a materializer-shaped ref that the registry
    never registered is a new credential path outside registration.
    """

    from moonmind.omnigent.harness_platform.materializers import (
        BUILTIN_MATERIALIZERS,
    )

    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "moonmind/omnigent").rglob("*.py")):
        relative = str(path.relative_to(REPO_ROOT))
        if relative in CREDENTIAL_REGISTRATION_MODULES:
            continue
        for literal in _string_literals(relative):
            if (
                _MATERIALIZER_REF_PATTERN.match(literal)
                and literal not in BUILTIN_MATERIALIZERS
            ):
                offenders.append(f"{relative}: {literal}")
    assert not offenders, (
        "credential materialization handles are registered only in "
        f"{CREDENTIAL_REGISTRATION_MODULES}: {offenders}"
    )


def test_no_hidden_legacy_compose_overlay_or_startup_script() -> None:
    """Compose topology and provider startup scripts stay discoverable."""

    from moonmind.omnigent.legacy_retirement import assert_inventory_is_complete

    compose_files = sorted(
        path.name
        for path in REPO_ROOT.glob("docker-compose*.y*ml")
    )
    assert compose_files == sorted(ALLOWED_COMPOSE_FILES), (
        "a new Compose file is hidden deployment topology; consolidate it or "
        f"declare it: {compose_files}"
    )
    # Any provider-specific Compose service, profile, or startup script that is
    # not classified with a retirement class fails here.
    assert_inventory_is_complete()


def test_architecture_exception_modules_are_inventoried_legacy_paths() -> None:
    """A boundary exemption may only shelter a classified legacy component."""

    from moonmind.omnigent.legacy_retirement import (
        ARCHITECTURE_BOUNDARY_EXCEPTIONS,
        RetirementClass,
        get_retirement_record,
    )

    for exception in ARCHITECTURE_BOUNDARY_EXCEPTIONS:
        record = get_retirement_record(exception.retirement_path_id)
        assert record.retirement_class is not RetirementClass.REMOVED, (
            f"architecture exception {exception.exception_id} is owned by a "
            "removed retirement row; delete the exemption with the code"
        )


# Negative fixtures: each retirement guard must actually reject the shape it
# forbids, so a future refactor cannot leave it vacuously passing.


@pytest.mark.parametrize(
    "source",
    (
        "class ClaudeSessionCoordinator:\n    pass\n",
        "class OpenCodeHostLifecycle:\n    pass\n",
        "class CodexManagedRuntime:\n    pass\n",
    ),
)
def test_provider_lifecycle_coordinator_negative_fixtures(source: str) -> None:
    assert _provider_lifecycle_classes(ast.parse(source))


@pytest.mark.parametrize(
    "source",
    (
        "DEFAULT_WORKFLOW_RUNTIME = 'codex_cli'",
        "SOME_DEFAULT_RUNTIME = 'claude_code'",
    ),
)
def test_direct_default_selection_negative_fixtures(source: str) -> None:
    assert _direct_default_assignments(
        ast.parse(source), frozenset({"codex_cli", "claude_code"})
    )


@pytest.mark.parametrize(
    "source",
    (
        "try:\n    generic()\nexcept Exception:\n    realizer = 'codex-profile-bound@1'\n",
        "try:\n    generic()\nexcept Exception:\n    return 'codex_cli'\n",
    ),
)
def test_implicit_fallback_negative_fixtures(source: str) -> None:
    assert _legacy_fallback_constants(
        ast.parse(source),
        frozenset({"codex-profile-bound@1", "codex_cli"}),
    )


def test_implicit_fallback_detector_ignores_non_handler_literals() -> None:
    # Recording the realizer a plan already selected is not a fallback.
    source = "recorded = 'codex-profile-bound@1'\n"
    assert not _legacy_fallback_constants(
        ast.parse(source), frozenset({"codex-profile-bound@1"})
    )


@pytest.mark.parametrize(
    "source",
    (
        "IMAGE = os.getenv('OMNIGENT_SHARED_HOST_IMAGE_REF')",
        "IMAGE = os.environ['OMNIGENT_SHARED_HOST_IMAGE_REF']",
        "IMAGE = environment.get('OMNIGENT_SHARED_HOST_IMAGE_REF', '')",
    ),
)
def test_shared_image_resolution_negative_fixtures(source: str) -> None:
    assert _reads_environment_key(ast.parse(source), SHARED_IMAGE_ENV)


def test_shared_image_detector_ignores_a_named_replacement_hint() -> None:
    # Naming the canonical variable as an operator hint is not resolution.
    source = "HINT = Obsolete(replacement='OMNIGENT_SHARED_HOST_IMAGE_REF')"
    assert not _reads_environment_key(ast.parse(source), SHARED_IMAGE_ENV)


def test_unregistered_materializer_ref_is_a_new_credential_path() -> None:
    from moonmind.omnigent.harness_platform.materializers import (
        BUILTIN_MATERIALIZERS,
    )

    assert _MATERIALIZER_REF_PATTERN.match("gemini-oauth-home@1")
    assert "gemini-oauth-home@1" not in BUILTIN_MATERIALIZERS
