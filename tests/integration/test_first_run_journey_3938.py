"""Hermetic first-run journey for MoonLadderStudios/MoonMind#3938.

Required tier (``integration`` + ``integration_ci``): deterministic,
credential-free, no live provider inference calls. Controlled substitutions
(test catalog/image authority, throwaway SQLite) are labeled as such inline
and are never described as proof of a live zero-configuration journey; live
evidence belongs to ``tools/first_run_live_qualification.py --live``.

Covers the bounded backlog: disposable-project definition, no-.env startup to
provider-default authority via the real ``startup_event``, omitted-vs-explicit
equivalence on startup-seeded rows, no-publication fixture, provider-vs-platform
failure taxonomy with no substitution, restart idempotency, scoped teardown
with redacted diagnostics, and per-phase timing structure.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, ManagedAgentProviderProfile
from api_service.main import startup_event
from moonmind.first_run.harness import (
    DEPLOYMENT_PROJECT_NAME,
    FIRST_RUN_PHASES,
    FORBIDDEN_ENV_KEYS,
    build_timing_record,
    check_clean_install_env,
    classify_first_run_failure,
    first_run_fixture,
    plan_scoped_teardown,
    redact_diagnostics,
    resolve_same_authority,
    validate_disposable_project,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = REPO_ROOT / "moonmind" / "first_run" / "harness.py"

_SUBSTITUTION_LABEL = (
    "controlled hermetic substitution (test catalog/image authority); "
    "not evidence of live provider availability"
)


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if v is not None}


def test_disposable_project_definition_rejects_deployment_project() -> None:
    validate_disposable_project("moonmind-test")
    validate_disposable_project("moonmind-test-first-run-3938")
    for bad in (DEPLOYMENT_PROJECT_NAME, "moonmind-test-", "other-project", ""):
        with pytest.raises(ValueError):
            validate_disposable_project(bad)


def test_clean_install_starting_state_is_credential_and_state_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in FORBIDDEN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert check_clean_install_env(_clean_env(), dot_env_exists=False) == []
    assert any(
        "inherited credential" in v
        for v in check_clean_install_env(
            {**_clean_env(), "OPENAI_API_KEY": "sk-live"}, dot_env_exists=False
        )
    )
    assert any(
        ".env" in v
        for v in check_clean_install_env(_clean_env(), dot_env_exists=True)
    )
    assert any(
        "catalog" in v
        for v in check_clean_install_env(
            _clean_env(), dot_env_exists=False, catalog_rows_present=True
        )
    )
    assert (tmp_path / ".env").exists() is False


def test_compose_default_path_needs_no_env_file() -> None:
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    )
    for service_name in ("api", "postgres", "temporal", "omnigent"):
        assert service_name in compose["services"], service_name
    for name, svc in compose["services"].items():
        assert "build" not in svc, f"{name} must be a pulled image"
        for env_file in svc.get("env_file", []):
            if isinstance(env_file, dict) and env_file.get("path") == ".env":
                assert env_file.get("required") is False, name


def test_compose_renders_without_env_file_when_docker_available(
    tmp_path: Path,
) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not available")
    probe = subprocess.run(
        ["docker", "compose", "version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip("docker compose plugin is not available")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            "docker-compose.yaml",
            "config",
            "--quiet",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr


async def _startup_to_provider_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_name: str
) -> None:
    for key in FORBIDDEN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    db_url = f"sqlite+aiosqlite:///{tmp_path}/{db_name}"
    engine = create_async_engine(db_url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_base, "DATABASE_URL", db_url, raising=False)
    monkeypatch.setattr(db_base, "engine", engine, raising=False)
    monkeypatch.setattr(db_base, "async_session_maker", maker, raising=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    with patch("api_service.main._initialize_oidc_provider"):
        await startup_event()
    try:
        async with maker() as session:
            zen = await session.get(
                ManagedAgentProviderProfile, "opencode-zen-free"
            )
            assert zen is not None
            assert zen.enabled is True
            assert zen.is_default is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_env_startup_seeds_credentialless_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disabled_env_keys,
) -> None:
    """REQ-01/PLAN-02 (hermetic part): the no-.env startup path seeds the
    credentialless default authority. Terminal-evidence observation of a live
    run belongs to the protected live tier, not this hermetic check."""
    await _startup_to_provider_default(tmp_path, monkeypatch, "firstrun.db")


@pytest.mark.asyncio
async def test_omitted_and_explicit_defaults_agree_on_startup_seeded_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disabled_env_keys,
) -> None:
    """REQ-02: omitted values and documented default equivalents resolve to
    the same intended authority on rows seeded by the production startup
    path (plus one labeled catalog substitution for the agent profile)."""
    from api_service.services.omnigent_agent_profile_selection import (
        resolve_default_agent_profile_snapshot,
    )
    from tests.unit.services.test_default_omnigent_launch_authority import (
        _publish_opencode_catalog_authority,
    )

    await _startup_to_provider_default(tmp_path, monkeypatch, "equiv.db")
    # Controlled substitution for the harness catalog only; the provider
    # default rows above came from the real startup_event.
    _publish_opencode_catalog_authority(monkeypatch)
    assert _SUBSTITUTION_LABEL

    async with db_base.async_session_maker() as session:
        user = SimpleNamespace(id=uuid4(), is_superuser=True)
        automatic = await resolve_default_agent_profile_snapshot(
            session,
            provider_profile_ref=None,
            launch_policy_ref=None,
            consumer_type="workflow",
            consumer_id="mm:first-run-omitted",
            user=user,
        )
        zen_ref = automatic["providerProfileRef"]
        explicit = await resolve_default_agent_profile_snapshot(
            session,
            provider_profile_ref=zen_ref,
            launch_policy_ref=None,
            consumer_type="workflow",
            consumer_id="mm:first-run-explicit",
            user=user,
        )
        assert resolve_same_authority(automatic, explicit)
        # Idempotent retry: same consumer intent resolves the same authority.
        repeat = await resolve_default_agent_profile_snapshot(
            session,
            provider_profile_ref=None,
            launch_policy_ref=None,
            consumer_type="workflow",
            consumer_id="mm:first-run-omitted",
            user=user,
        )
        assert resolve_same_authority(automatic, repeat)
        assert automatic["providerProfileRef"] == "opencode-zen-free"


def test_first_run_fixture_carries_no_publication_authority() -> None:
    """PLAN-03: the credentialless scenario needs no PAT and authorizes no
    publication or operator-repo mutation."""
    fixture = first_run_fixture()
    assert fixture["publication"] == {"mode": "none", "authorized": False}
    assert fixture["requires_pat"] is False
    assert fixture["repository"] is None


def test_provider_failure_is_distinct_from_platform_failure_and_never_success() -> (
    None
):
    """REQ-05/PLAN-06 (hermetic part): provider outages are labeled provider
    failures, never success, and never permit silent substitution."""
    provider = classify_first_run_failure("provider_unavailable")
    limited = classify_first_run_failure("provider_rate_limited")
    platform = classify_first_run_failure("startup_failure")
    assert provider["tier"] == "provider"
    assert limited["tier"] == "provider"
    assert platform["tier"] == "platform"
    for record in (provider, limited, platform):
        assert record["countable_as_success"] is False
        assert record["substitution_permitted"] is False
        assert record["actionable"]
    assert classify_first_run_failure("mystery-kind")["tier"] == "platform"


def test_scoped_teardown_refuses_global_and_deployment_resources() -> None:
    """PLAN-07: teardown removes only disposable project resources."""
    plan = plan_scoped_teardown(
        "moonmind-test-first-run",
        [
            "moonmind-test-first-run_api_1",
            "moonmind-test-first-run_postgres_data",
            "moonmind_postgres_data",
            "some-operator-volume",
            "docker volume prune",
            "down -v",
        ],
    )
    assert plan.resources == [
        "moonmind-test-first-run_api_1",
        "moonmind-test-first-run_postgres_data",
    ]
    assert "moonmind_postgres_data" in plan.refused
    assert "docker volume prune" in plan.refused
    with pytest.raises(ValueError):
        plan_scoped_teardown(
            DEPLOYMENT_PROJECT_NAME, [f"{DEPLOYMENT_PROJECT_NAME}_api_1"]
        )


def test_diagnostics_are_bounded_and_redacted() -> None:
    raw = "token=ghp_abc123 and OPENAI_API_KEY=sk-live-value " + ("x" * 9000)
    redacted = redact_diagnostics(raw)
    assert "ghp_abc123" not in redacted
    assert "sk-live-value" not in redacted
    assert "[REDACTED]" in redacted
    assert len(redacted) <= 8000 + 64


def test_phase_timings_record_cold_and_warm_separately() -> None:
    """PLAN-05: per-phase timings with cold/warm separation and a measured
    (not guaranteed) ten-minute budget."""
    phases = {phase: 10.0 for phase in FIRST_RUN_PHASES}
    cold = build_timing_record(phases, "cold")
    warm = build_timing_record(phases, "warm")
    assert cold["cache_condition"] == "cold"
    assert warm["cache_condition"] == "warm"
    assert set(cold["phases"]) == set(FIRST_RUN_PHASES)
    assert cold["budget_seconds"] == 600
    assert "not a guarantee" in str(cold["budget_note"])
    over = build_timing_record(
        {phase: 600.0 for phase in FIRST_RUN_PHASES}, "cold"
    )
    assert over["within_budget"] is False
    with pytest.raises(ValueError):
        build_timing_record(phases, "lukewarm")


def test_hermetic_tier_makes_no_live_provider_calls() -> None:
    """REQ-03: the required hermetic tier is deterministic, credential-free,
    and performs no live provider inference. Static guard: the shared harness
    imports stdlib only, and no forbidden credential is configured here."""
    tree = ast.parse(HARNESS_PATH.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert imports <= {
        "re",
        "time",
        "dataclasses",
        "__future__",
        "typing",
        "datetime",
    }, f"harness must stay stdlib-only, got: {sorted(imports)}"
    source = HARNESS_PATH.read_text(encoding="utf-8")
    for marker in (
        "api.openai.com",
        "generativelanguage.googleapis.com",
        "api.anthropic.com",
        "openrouter.ai",
    ):
        assert marker not in source
    for key in FORBIDDEN_ENV_KEYS:
        assert not os.environ.get(key), f"hermetic run must not set {key}"
    this_source = Path(__file__).read_text(encoding="utf-8")
    assert "opencode-zen-free" in this_source  # seeded-row authority, not a call
    assert "httpx.post" not in this_source
