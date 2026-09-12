"""Faultlab/production boundary: test-only runners stay out of the prod image.

Source issue: MoonLadderStudios/MoonMind#3958.

The fault-injection scenario engines, YAML fixtures, and command drivers live
under ``tools/omnigent_faultlab/`` (test/developer tooling) and must not ship
in the production ``moonmind`` package or deployable image. Production-needed
evidence validation (``conformance``, ``exact_artifact_conformance``,
``live_verification_health``) stays in
``moonmind/omnigent/`` and the runtime catalog/readiness path must import from
there — never from ``tests`` or the test-only harness. (``embedded_acceptance``
was removed with the retired embedded host transport,
MoonLadderStudios/MoonMind#3955, and is intentionally absent from this list.)
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

OLD_FAULTLAB_DIR = REPO_ROOT / "moonmind" / "omnigent" / "faultlab"
NEW_FAULTLAB_DIR = REPO_ROOT / "tools" / "omnigent_faultlab"
NEW_SCENARIOS_DIR = NEW_FAULTLAB_DIR / "scenarios"

PROD_ROOTS = (
    REPO_ROOT / "moonmind",
    REPO_ROOT / "api_service",
    REPO_ROOT / "services",
)

RUNTIME_VALIDATOR_MODULES = (
    "moonmind/omnigent/conformance.py",
    "moonmind/omnigent/exact_artifact_conformance.py",
    "moonmind/omnigent/live_verification_health.py",
)


def _imports(relative_path: str) -> tuple[str, ...]:
    module = relative_path.removesuffix(".py").replace("/", ".")
    tree = ast.parse((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level:
                package = module.split(".")[: -node.level]
                imported.append(".".join([*package, node.module]))
            else:
                imported.append(node.module)
    return tuple(imported)


def test_faultlab_lives_in_tools_not_in_production_package() -> None:
    assert not OLD_FAULTLAB_DIR.exists(), (
        "moonmind/omnigent/faultlab must not exist: scenario engines belong "
        "under tools/ per #3958"
    )
    assert (NEW_FAULTLAB_DIR / "__init__.py").is_file()
    assert (NEW_FAULTLAB_DIR / "image_smoke.py").is_file()
    assert (NEW_FAULTLAB_DIR / "harness.py").is_file()
    scenarios = sorted(NEW_SCENARIOS_DIR.glob("*/fault-scenario.yaml"))
    assert len(scenarios) >= 6, (
        f"expected the packaged YAML corpus under {NEW_SCENARIOS_DIR}, "
        f"found {len(scenarios)} scenario files"
    )


def test_production_image_copy_rules_exclude_test_tooling() -> None:
    dockerfile = (REPO_ROOT / "api_service" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY moonmind /app/moonmind/" in dockerfile
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY"):
            continue
        assert "faultlab" not in stripped, (
            f"production image must not COPY faultlab: {stripped}"
        )
        # tools/ is never copied into the deployable runtime (/app); the
        # image-smoke workflow mounts only tools/ at /src/tools instead. Build-stage copies
        # into /build (e.g. the frontend-builder verify script) are not
        # shipped to the runtime and are out of scope here.
        if " /app/" in stripped:
            assert not stripped.startswith("COPY tools") or stripped == "COPY tools/verify_deployed_ui_assets.py /app/tools/verify_deployed_ui_assets.py", (
                f"production image must not COPY tools/ to /app: {stripped}"
            )


def test_pyproject_does_not_package_test_tooling() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'include = "moonmind"' in pyproject
    assert "omnigent_faultlab" not in pyproject
    assert "faultlab" not in pyproject


def test_runtime_validators_do_not_require_faultlab() -> None:
    for relative in RUNTIME_VALIDATOR_MODULES:
        assert (REPO_ROOT / relative).is_file(), f"missing runtime module {relative}"
        for imported in _imports(relative):
            assert "faultlab" not in imported, (
                f"{relative} depends on test-only faultlab via {imported}"
            )
    # The validators import cleanly with no faultlab present. Use importlib so
    # this module keeps a single static import style (from-imports below) and
    # the code-quality mixed import-style check stays clean.
    import importlib

    for validator in (
        "moonmind.omnigent.conformance",
        "moonmind.omnigent.exact_artifact_conformance",
        "moonmind.omnigent.live_verification_health",
    ):
        importlib.import_module(validator)


def test_catalog_readiness_imports_from_runtime_not_tests() -> None:
    catalog = "api_service/api/routers/omnigent_catalog.py"
    imported = _imports(catalog)
    assert "moonmind.omnigent.exact_artifact_conformance" in imported
    assert "moonmind.omnigent.live_verification_health" in imported
    for name in imported:
        assert not name.startswith("tests"), (
            f"{catalog} imports test tree via {name}"
        )
        assert "faultlab" not in name, (
            f"{catalog} imports test-only faultlab via {name}"
        )


def test_no_production_module_imports_tests_or_faultlab() -> None:
    offenders: list[str] = []
    for root in PROD_ROOTS:
        for path in sorted(root.rglob("*.py")):
            relative = str(path.relative_to(REPO_ROOT))
            for imported in _imports(relative):
                if imported.split(".")[0] == "tests":
                    offenders.append(f"{relative} -> {imported}")
                if imported in (
                    "tools.omnigent_faultlab",
                    "moonmind.omnigent.faultlab",
                ) or imported.startswith(
                    ("tools.omnigent_faultlab.", "moonmind.omnigent.faultlab.")
                ):
                    offenders.append(f"{relative} -> {imported}")
                if "moonmind.omnigent.faultlab" in imported:
                    offenders.append(f"{relative} -> {imported}")
    assert not offenders, (
        "production modules must not import tests or the test-only "
        f"faultlab harness: {offenders}"
    )


def test_no_fault_injection_route_in_api_service() -> None:
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "api_service").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "faultlab" in source or "image_smoke" in source:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"api_service must not reference fault injection runners: {offenders}"
    )


def test_old_faultlab_import_path_has_no_remaining_references() -> None:
    # This boundary test itself names the old path to assert its removal.
    allowed = {"tests/unit/omnigent/test_faultlab_boundary.py"}
    offenders: list[str] = []
    for suffix in (".py", ".yml", ".yaml"):
        pattern = f"**/*{suffix}"
        for path in sorted(REPO_ROOT.glob(pattern)):
            if ".git/" in str(path):
                continue
            relative = str(path.relative_to(REPO_ROOT))
            if relative in allowed:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "moonmind.omnigent.faultlab" in source or (
                "moonmind/omnigent/faultlab" in source
            ):
                offenders.append(relative)
    assert not offenders, (
        "old moonmind.omnigent.faultlab import path has remaining "
        f"references: {offenders}"
    )


def test_image_smoke_workflow_mounts_harness_and_records_provenance() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "omnigent-fault-image-smoke.yml"
    ).read_text(encoding="utf-8")
    assert "tools.omnigent_faultlab.image_smoke" in workflow
    assert "moonmind.omnigent.faultlab" not in workflow
    # Mount-based execution: harness from the checkout, prod modules from image.
    # Only tools/ is mounted (never the whole checkout) so /src/moonmind and
    # /src/api_service stay off PYTHONPATH; the driver additionally fails unless
    # production imports resolve under /app.
    assert "-v \"$PWD/tools:/src/tools:ro\"" in workflow or (
        '-v "$PWD/tools:/src/tools:ro"' in workflow
    )
    assert '"$PWD:/src:ro"' not in workflow and "'$PWD:/src:ro'" not in workflow
    assert "PYTHONPATH=/app:/src" in workflow
    assert "--image-ref" in workflow
    assert "--role" in workflow


def test_docs_point_to_relocated_tooling() -> None:
    suite = (
        REPO_ROOT / "docs" / "Omnigent" / "OmnigentFaultInjectionSuite.md"
    ).read_text(encoding="utf-8")
    assert "tools/omnigent_faultlab" in suite
    assert "moonmind/omnigent/faultlab" not in suite


def test_missing_malformed_evidence_still_fails_closed() -> None:
    from moonmind.omnigent.exact_artifact_conformance import (
        ExactArtifactConformanceError,
        assert_exact_artifact_evidence,
        evaluate_exact_artifact_conformance,
    )
    from moonmind.omnigent.live_verification_health import (
        LiveVerificationHealthError,
        assert_live_health_projection,
        evaluate_live_verification_health,
    )

    with pytest.raises(ExactArtifactConformanceError):
        evaluate_exact_artifact_conformance({}, required_digests={})
    with pytest.raises(ExactArtifactConformanceError):
        assert_exact_artifact_evidence({"verdict": "failed", "failures": [{}]})

    with pytest.raises(LiveVerificationHealthError):
        evaluate_live_verification_health(
            runner={},
            queue={},
            latest_run=None,
            manifest=None,
            deployed_commit="",
            required_digests={},
        )
    offline = evaluate_live_verification_health(
        runner={"status": "offline", "busy": False},
        queue={},
        latest_run=None,
        manifest=None,
        deployed_commit="abc123",
        required_digests={},
    )
    assert offline["tier1Ready"] is True
    assert offline["protectedTierReady"] is False
    assert offline["rolloutReady"] is False

    stale = dict(offline)
    stale["generatedAt"] = "2000-01-01T00:00:00+00:00"
    with pytest.raises(LiveVerificationHealthError):
        assert_live_health_projection(
            stale, expected_commit="abc123", now=datetime.now(timezone.utc)
        )


def test_relocated_harness_still_runs_deterministically() -> None:
    from tools.omnigent_faultlab.generator import generate_plan, is_deterministic
    from tools.omnigent_faultlab.harness import run_plan
    from tools.omnigent_faultlab.image_smoke import run_image_fault_matrix
    from tools.omnigent_faultlab.invariants import violations

    plan = generate_plan(7)
    assert is_deterministic(plan)
    assert violations(run_plan(plan)) == []
    report = run_image_fault_matrix(seed_count=4)
    assert report.ok
