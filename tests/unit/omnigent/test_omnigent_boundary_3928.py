"""Omnigent boundary guards: test-runner separation and production evidence path.

Source issue: MoonLadderStudios/MoonMind#3928 (children #3954 inventory,
#3958 test-runner separation).

The responsibility inventory lives in
``docs/Omnigent/OmnigentBoundaryResponsibilityMap.md``. These tests lock its
separation claims in: tool- and test-owned runners must stay out of the
deployed runtime, while the production qualification gates named by the
inventory must stay in the production path.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

PRODUCTION_ROOTS = ("moonmind", "api_service", "services")

# Tool- and test-owned runners that must never be part of the deployed path.
# Deliberately excludes modules production legitimately consumes:
# - moonmind.omnigent.conformance / exact_artifact_conformance /
#   live_verification_health are production qualification gates imported by
#   omnigent_catalog.py (inventory §4.3).
# - moonmind.omnigent.workflow_chat_acceptance backs the production
#   retirement guard in legacy_retirement.py (inventory §4.5).
TEST_ONLY_IMPORT_PREFIXES = (
    "tools",
    "tools.",
    "moonmind.omnigent.faultlab",
    "moonmind.omnigent.cutover_conformance",
    "moonmind.omnigent.remediation_matrix_conformance",
    "moonmind.omnigent.embedded_acceptance",
)

# Packages whose own internal imports are not production leakage.
TEST_ONLY_HOME_PACKAGES = (
    "moonmind.omnigent.faultlab",
    "moonmind.omnigent.cutover_conformance",
    "moonmind.omnigent.remediation_matrix_conformance",
    "moonmind.omnigent.embedded_acceptance",
)


def _imports_of(path: Path) -> tuple[str, ...]:
    current_module = (
        str(path.relative_to(REPO_ROOT)).removesuffix(".py").replace("/", ".")
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
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


def _is_test_only_home(path: Path) -> bool:
    module = (
        str(path.relative_to(REPO_ROOT)).removesuffix(".py").replace("/", ".")
    )
    return module in TEST_ONLY_HOME_PACKAGES or module.startswith(
        "moonmind.omnigent.faultlab."
    )


def test_production_runtime_does_not_import_test_only_runners() -> None:
    offenders: list[str] = []
    for root in PRODUCTION_ROOTS:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if _is_test_only_home(path):
                continue
            for imported in _imports_of(path):
                if imported == "tools" or imported.startswith(
                    TEST_ONLY_IMPORT_PREFIXES[1:]
                ):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)} -> {imported}"
                    )
    assert not offenders, (
        "production runtime imports test-only runners: " f"{offenders}"
    )


def test_production_qualification_gates_stay_in_production_path() -> None:
    catalog = REPO_ROOT / "api_service/api/routers/omnigent_catalog.py"
    imported = _imports_of(catalog)
    for gate in (
        "moonmind.omnigent.conformance",
        "moonmind.omnigent.exact_artifact_conformance",
        "moonmind.omnigent.live_verification_health",
    ):
        assert gate in imported, (
            f"omnigent_catalog.py no longer imports production qualification "
            f"gate {gate}; evidence validation must stay in the production "
            f"path (MoonLadderStudios/MoonMind#3928 §4.3)"
        )


def test_boundary_inventory_names_each_separation_claim() -> None:
    inventory = REPO_ROOT / "docs/Omnigent/OmnigentBoundaryResponsibilityMap.md"
    assert inventory.is_file(), (
        "boundary responsibility inventory is missing "
        "(MoonLadderStudios/MoonMind#3928 child #3954)"
    )
    text = inventory.read_text(encoding="utf-8")
    assert "f04b0354fb5344c1ea8b92795ceb6760a9ad7595" in text
    for module in (
        *TEST_ONLY_IMPORT_PREFIXES[2:],
        "moonmind.omnigent.conformance",
        "moonmind.omnigent.exact_artifact_conformance",
        "moonmind.omnigent.live_verification_health",
        "moonmind.omnigent.bridge_embedded",
        "moonmind.omnigent.workflow_chat_facade",
        "moonmind.omnigent.native_ui",
        "moonmind.omnigent.native_ui_compat",
    ):
        path_form = module.replace(".", "/") + ".py"
        assert module in text or path_form in text, (
            f"boundary inventory does not cover {module}; keep the inventory "
            f"and the guards above in agreement "
            f"(MoonLadderStudios/MoonMind#3928)"
        )
