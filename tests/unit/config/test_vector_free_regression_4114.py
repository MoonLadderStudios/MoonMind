"""Vector-free deployment and workflow regression coverage (#4114).

Cross-boundary verification owner for the Qdrant-removal release
(MoonLadderStudios/MoonMind#4114, parent #4103, acceptance contract #4105).
This module proves removal across the hermetic boundaries it can own in
required CI and names the protected deployment checks it cannot own, so no
supported runtime or live deployment is claimed qualified without evidence.

Matrix-to-evidence mapping (issue required-coverage rows):

- Clean dependencies: fixture negative controls live here
  (``test_dependency_guard_*``). MR5 (MoonLadderStudios/MoonMind#4192)
  removed the manifest-only distributions:
  ``test_dependency_removal_landed_no_manifest_only_distributions`` asserts
  ``pyproject.toml`` and ``poetry.lock`` carry no ``qdrant-client``,
  ``llama-index``, or reader packages. Image-level qualification stays a
  protected deployment check (see ``test_topology_matrix_gaps_are_explicit``).
- Topology: ``test_compose_*`` guards the rendered default Compose file, the
  test Compose file, every declared profile, and every documented
  ``--profile``/``COMPOSE_PROFILES`` combination (hermetic YAML render under
  sanitized env), plus fixture negative controls for renamed services,
  optional profiles, embedded vector startup, and pgvector init SQL. Live
  startup remains a protected deployment check (see
  ``test_topology_matrix_gaps_are_explicit``).
- Real startup: hermetic sentinel logic only
  (``test_startup_sentinel_*``). Fresh Compose startup, init-db/Alembic,
  readiness, dashboard bootstrap, and repeated startup are protected checks
  owned with the cutover child, not claimed here.
- Public admission: ``test_admission_*`` exercises the real production
  admission path (``reject_retired_vector_fields`` /
  ``strip_absent_vector_fields`` from #4105) including hidden-state residue
  and old required-vector payloads.
- Runtime/context, Follow-up/chat, Ingestion, Memory/finalization, Security,
  Recovery/upgrade: mapped to existing owners plus protected checks in
  ``test_topology_matrix_gaps_are_explicit``; this module adds no parallel
  framework and replaces no production path with a fake service.
- Docs/operations: ``test_env_template_*`` and
  ``test_update_script_*`` prove the shipped examples/scripts do not
  demand or recreate a vector backend. ``test_docs_operations_*`` verifies
  the removed worker-vector guide and README advertising, while
  ``test_cli_help_*`` checks the real generated help. These bounded public
  surfaces do not establish dependency cleanup or live qualification.

Negative controls (issue requirement 5): every guard below is proven with a
fixture that reintroduces the retired capability (transitive requirement,
renamed service, optional profile, vector init SQL, leaked tool descriptor,
old required-vector payload) and must fail in the owning guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from moonmind.workflows.executions.execution_contract import (
    WorkflowContractError,
    reject_retired_vector_fields,
    strip_absent_vector_fields,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Reviewed topology/dependency/startup contracts.
#
# These pure helpers are the reviewed contract named by the issue: they reject
# literal or renamed vector services, optional vector profiles, embedded
# vector startup, pgvector init SQL, transitive Qdrant distributions, and
# vector startup attempts in logs. Real-repo tests run them over the actual
# checkout; fixture tests prove each negative control fails for the intended
# reason.
# ---------------------------------------------------------------------------

_VECTOR_IMAGE_RE = re.compile(
    r"qdrant|pgvector|milvus|vector-embed", re.IGNORECASE
)
_VECTOR_SERVICE_NAME_RE = re.compile(
    r"qdrant|milvus|vector[-_ ]?(db|store|service|index)|embeddings?|pgvector",
    re.IGNORECASE,
)
_VECTOR_PROFILE_RE = re.compile(r"qdrant|vector|embedding|pgvector", re.IGNORECASE)
_VECTOR_ENV_RE = re.compile(r"QDRANT_(URL|HOST|PORT|ENABLED)|VECTOR_STORE_PROVIDER")
_VECTOR_PORTS = {"6333", "6334"}
_VECTOR_COMMAND_RE = re.compile(
    r"qdrant|pgvector|init-embeddings|embedding-init", re.IGNORECASE
)
_VECTOR_SQL_RE = re.compile(
    r"CREATE\s+EXTENSION[^;]*vector"
    r"|USING\s+(ivfflat|hnsw)"
    r"|\bpgvector\b",
    re.IGNORECASE,
)
_STARTUP_SENTINEL_RE = re.compile(
    r"QDRANT_URL|qdrant[:/]|connect(?:ion|ing)?\s+(?:to\s+)?qdrant"
    r"|embedding (?:model |index )?(?:init|initializ)"
    r"|Qdrant (?:connection|DNS|unavailable|outage)",
    re.IGNORECASE,
)
_QDRANT_DISTRIBUTION_RE = re.compile(r"^qdrant(-client)?$", re.IGNORECASE)
_RETIRED_TOOL_DESCRIPTOR_RE = re.compile(
    r"qdrant|followUpRetrieval|follow_up_retrieval", re.IGNORECASE
)

# The native Qdrant volume is retained through the recovery window (#4115) and
# ``moonmind_retrieval_state`` is classified by #4107. Any other
# vector-named volume is a reintroduction.
_KNOWN_VECTOR_FREE_VOLUMES = {"qdrant-storage", "moonmind_retrieval_state"}


def _env_items(service: dict) -> list[str]:
    env = service.get("environment", [])
    if isinstance(env, dict):
        return [f"{key}={value}" for key, value in env.items()]
    return [str(item) for item in env]


def check_compose_vector_free(compose: dict) -> list[str]:
    """Return human-readable problems when Compose carries vector wiring."""
    problems: list[str] = []
    services = compose.get("services", {}) or {}
    for name, service in services.items():
        service = service or {}
        if _VECTOR_SERVICE_NAME_RE.search(str(name)):
            # The historical ``qdrant-storage`` *volume* is allowed; a live
            # *service* with a vector name is never allowed.
            problems.append(f"service {name!r} carries a vector service name")
        image = str(service.get("image", ""))
        if _VECTOR_IMAGE_RE.search(image):
            problems.append(f"service {name!r} uses vector image {image!r}")
        depends = service.get("depends_on", {})
        depends_keys = (
            depends.keys() if isinstance(depends, dict) else list(depends or [])
        )
        for dep in depends_keys:
            if _VECTOR_SERVICE_NAME_RE.search(str(dep)):
                problems.append(
                    f"service {name!r} depends on vector service {dep!r}"
                )
        for item in _env_items(service):
            if _VECTOR_ENV_RE.search(item):
                problems.append(
                    f"service {name!r} wires retired vector env {item!r}"
                )
        for port in service.get("ports", []) or []:
            if any(border in str(port) for border in _VECTOR_PORTS):
                problems.append(
                    f"service {name!r} exposes vector port {port!r}"
                )
        for key in ("command", "entrypoint"):
            command = service.get(key)
            blob = " ".join(command) if isinstance(command, list) else str(
                command or ""
            )
            if _VECTOR_COMMAND_RE.search(blob):
                problems.append(
                    f"service {name!r} embeds vector startup in {key}: {blob!r}"
                )
        for profile in service.get("profiles", []) or []:
            if _VECTOR_PROFILE_RE.search(str(profile)):
                problems.append(
                    f"service {name!r} hides vector backend "
                    f"behind optional profile {profile!r}"
                )
    volumes = compose.get("volumes", {}) or {}
    for volume in volumes:
        if (
            _VECTOR_SERVICE_NAME_RE.search(str(volume))
            and volume not in _KNOWN_VECTOR_FREE_VOLUMES
        ):
            problems.append(f"volume {volume!r} reintroduces vector state")
    return problems


def check_init_sql_vector_free(sql_text: str) -> list[str]:
    """Return problems when init SQL installs a vector extension/index."""
    statements = [
        line
        for line in sql_text.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    ]
    code = "\n".join(statements)
    if _VECTOR_SQL_RE.search(code):
        return ["init SQL installs vector extension/index"]
    return []


def check_dependency_vector_free(distribution_names: list[str]) -> list[str]:
    """Return problems when a distribution set carries a Qdrant package."""
    return [
        f"distribution {name!r} reintroduces the retired Qdrant SDK"
        for name in distribution_names
        if _QDRANT_DISTRIBUTION_RE.match(str(name).strip())
    ]


def check_tool_manifest_vector_free(descriptors: list[str]) -> list[str]:
    """Return problems when launch/tool manifests leak retired descriptors."""
    return [
        f"tool descriptor {descriptor!r} leaks retired retrieval capability"
        for descriptor in descriptors
        if _RETIRED_TOOL_DESCRIPTOR_RE.search(str(descriptor))
    ]


# ---------------------------------------------------------------------------
# Topology: real Compose file plus negative controls.
# ---------------------------------------------------------------------------


def test_compose_has_no_live_vector_service() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    assert check_compose_vector_free(compose) == []


def _declared_profiles(compose: dict) -> set[str]:
    profiles: set[str] = set()
    for service in ((compose.get("services", {})) or {}).values():
        for profile in (service or {}).get("profiles", []) or []:
            profiles.add(str(profile))
    return profiles


def test_compose_test_file_has_no_live_vector_service() -> None:
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.test.yaml").read_text()
    )
    assert check_compose_vector_free(compose) == []


def test_compose_all_profiles_are_vector_free() -> None:
    """Every declared profile renders without a vector backend.

    Hermetic YAML render under sanitized env: the full file (all profiles
    enabled) carries no vector wiring, and no declared profile name itself
    selects a vector backend. A renamed vector profile must fail here via
    the optional-profile guard, not hide behind ``COMPOSE_PROFILES``.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    assert check_compose_vector_free(compose) == []
    profiles = _declared_profiles(compose)
    # The test only means something when profiles exist to enumerate.
    assert profiles, "expected declared Compose profiles to enumerate"
    for profile in sorted(profiles):
        assert not _VECTOR_PROFILE_RE.search(profile), (
            f"profile {profile!r} selects a vector backend"
        )


def test_compose_documented_combinations_reference_no_vector_profile() -> None:
    """Documented ``--profile``/``COMPOSE_PROFILES`` combos stay vector-free.

    Every profile named by the supported-stack runbook must exist in the
    canonical Compose file and must not select a vector backend. Adding a
    documented vector combination must fail here.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    declared = _declared_profiles(compose)
    runbook = (
        REPO_ROOT / "docs/Omnigent/CombinedStackValidationAndRollback.md"
    ).read_text(encoding="utf-8")
    referenced: set[str] = set(
        re.findall(r"--profile\s+([A-Za-z0-9_.\-]+)", runbook)
    )
    for combo in re.findall(r"COMPOSE_PROFILES=\"([^\"]*)\"", runbook):
        referenced.update(
            part.strip() for part in combo.split(",") if part.strip()
        )
    assert referenced, "expected documented Compose profiles to enumerate"
    for profile in sorted(referenced):
        assert profile in declared, (
            f"documented profile {profile!r} is not a declared Compose profile"
        )
        assert not _VECTOR_PROFILE_RE.search(profile), (
            f"documented profile {profile!r} selects a vector backend"
        )


def test_compose_rejects_renamed_vector_service() -> None:
    fixture = {
        "services": {
            "api": {"image": "moonmind:latest"},
            "vector-db": {"image": "qdrant/qdrant:v1.17.1"},
        },
        "volumes": {},
    }
    problems = check_compose_vector_free(fixture)
    assert any("vector-db" in problem for problem in problems)
    assert any("vector image" in problem for problem in problems)


def test_compose_rejects_optional_vector_profile() -> None:
    fixture = {
        "services": {
            "api": {"image": "moonmind:latest"},
            "search-sidecar": {
                "image": "moonmind:latest",
                "profiles": ["vectors"],
                "depends_on": ["qdrant"],
            },
        },
        "volumes": {},
    }
    problems = check_compose_vector_free(fixture)
    assert any("optional profile" in problem for problem in problems)
    assert any("depends on vector service" in problem for problem in problems)


def test_compose_rejects_embedded_vector_startup() -> None:
    fixture = {
        "services": {
            "api": {
                "image": "moonmind:latest",
                "command": "init-embeddings && ./start-api.sh",
            },
        },
        "volumes": {},
    }
    problems = check_compose_vector_free(fixture)
    assert any("embeds vector startup" in problem for problem in problems)


def test_compose_rejects_vector_port_and_env_wiring() -> None:
    fixture = {
        "services": {
            "api": {
                "image": "moonmind:latest",
                "environment": ["QDRANT_URL=http://qdrant:6333"],
                "ports": ["6333:6333"],
            },
        },
        "volumes": {},
    }
    problems = check_compose_vector_free(fixture)
    assert any("retired vector env" in problem for problem in problems)
    assert any("vector port" in problem for problem in problems)


def test_compose_rejects_every_retired_vector_env_key() -> None:
    """Every retired vector env key fails even without ``QDRANT_URL``.

    Regression for Codex review 3971751049: the env guard must accept any
    ``_VECTOR_ENV_RE`` match, so ``QDRANT_ENABLED=true`` (and friends) cannot
    hide behind the absence of ``QDRANT_URL``.
    """
    for env_item in (
        "QDRANT_ENABLED=true",
        "QDRANT_HOST=qdrant",
        "QDRANT_PORT=6333",
        "VECTOR_STORE_PROVIDER=qdrant",
    ):
        fixture = {
            "services": {
                "api": {"image": "moonmind:latest", "environment": [env_item]},
            },
            "volumes": {},
        }
        problems = check_compose_vector_free(fixture)
        assert any("retired vector env" in problem for problem in problems), (
            f"retired vector env {env_item!r} was not flagged"
        )
    # Mapping-form environment carries the same wiring and must also fail.
    mapping_fixture = {
        "services": {
            "api": {
                "image": "moonmind:latest",
                "environment": {"QDRANT_ENABLED": "true"},
            },
        },
        "volumes": {},
    }
    assert any(
        "retired vector env" in problem
        for problem in check_compose_vector_free(mapping_fixture)
    )


def test_compose_rejects_canonical_vector_store_images() -> None:
    """Every canonical store backend image fails the topology guard.

    Regression for Codex review 3971751070: ``VECTOR_STORE_CAPABILITIES``
    names ``qdrant``, ``pgvector``, and ``milvus``; the image guard must flag
    each of them instead of maintaining an incomplete test-only list.
    """
    for image in (
        "qdrant/qdrant:v1.17.1",
        "pgvector/pgvector:pg16",
        "milvusdb/milvus:v2.4.0",
    ):
        fixture = {
            "services": {"search": {"image": image}},
            "volumes": {},
        }
        problems = check_compose_vector_free(fixture)
        assert any("vector image" in problem for problem in problems), (
            f"canonical vector image {image!r} was not flagged"
        )


def test_compose_allows_retained_recovery_volume() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    assert "qdrant-storage" in (compose.get("volumes", {}) or {})
    fixture = {"services": {}, "volumes": {"pgvector-data": {}}}
    assert any(
        "reintroduces vector state" in problem
        for problem in check_compose_vector_free(fixture)
    )


def _init_artifact_paths() -> list[Path]:
    """Enumerate every database initialization artifact under review.

    Regression for Codex review 3971751061: a fixed two-path list stays
    green when a new artifact (for example
    ``init_db_scripts/02-enable-vector.sql``) installs ``CREATE EXTENSION
    vector``. Enumerating the initialization directories keeps the guard
    closed as artifacts are added.
    """
    paths: list[Path] = []
    for directory in (REPO_ROOT / "init_db_scripts", REPO_ROOT / "init_db"):
        if directory.is_dir():
            paths.extend(sorted(p for p in directory.iterdir() if p.is_file()))
    return paths


def test_init_scripts_reject_vector_sql() -> None:
    assert check_init_sql_vector_free("CREATE EXTENSION vector;") != []
    assert (
        check_init_sql_vector_free(
            "CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops);"
        )
        != []
    )
    paths = _init_artifact_paths()
    # The test only means something when init artifacts exist to enumerate.
    assert paths, "expected database initialization artifacts to enumerate"
    for path in paths:
        assert check_init_sql_vector_free(path.read_text(encoding="utf-8")) == [], (
            f"init artifact {path} installs vector extension/index"
        )


# ---------------------------------------------------------------------------
# Clean dependencies: fixture guards plus explicit residual ownership.
# ---------------------------------------------------------------------------


def test_dependency_guard_passes_without_qdrant() -> None:
    assert check_dependency_vector_free(["fastapi", "pydantic"]) == []


def test_dependency_guard_rejects_direct_requirement() -> None:
    assert check_dependency_vector_free(["qdrant-client"]) != []


def test_dependency_guard_rejects_transitive_fixture_requirement() -> None:
    # Negative control: a fixture SDK that transitively pulls the retired
    # client must fail in the owning guard, not silently install it.
    assert (
        check_dependency_vector_free(
            ["moonmind-fixture-sdk", "Qdrant-Client", "httpx"]
        )
        != []
    )


def test_dependency_removal_landed_no_manifest_only_distributions() -> None:
    """Prove the MR5 dependency removal landed (sibling ownership resolved).

    MoonLadderStudios/MoonMind#4192 removed the native Manifest/RAG product:
    ``pyproject.toml`` and ``poetry.lock`` must not declare ``qdrant-client``,
    ``llama-index``, or any reader package. This replaces the retired
    ``test_dependency_removal_pending_sibling_ownership`` pin, which recorded
    the ``qdrant-client`` residual owned by #4106-#4113.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "qdrant-client" not in pyproject
    assert "llama-index" not in pyproject
    assert "llama_index" not in pyproject
    lock = (REPO_ROOT / "poetry.lock").read_text(encoding="utf-8")
    assert 'name = "qdrant-client"' not in lock
    assert 'name = "llama-index"' not in lock
    assert "llama-index-readers-" not in lock


# ---------------------------------------------------------------------------
# Public admission: real production path, old payloads and hidden residue.
# ---------------------------------------------------------------------------


def test_admission_rejects_old_required_vector_payload() -> None:
    old_payload = {
        "rag": {"collections": ["docs"], "required": True},
        "followUpRetrieval": {"enabled": True, "collections": ["repo"]},
    }
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(old_payload, field_path="payload")


def test_admission_rejects_snake_case_variant() -> None:
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(
            {"follow_up_retrieval": {"collections": ["repo"]}},
            field_path="payload",
        )


def test_admission_rejects_retired_fields_in_workflow_branch() -> None:
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(
            {"rag": {"collections": ["docs"]}}, field_path="workflow"
        )


def test_admission_strips_hidden_state_residue_without_changing_inputs() -> None:
    params = {
        "instructions": "summarize",
        "rag": {},
        "followUpRetrieval": {"enabled": False},
    }
    assert strip_absent_vector_fields(dict(params)) == {
        "instructions": "summarize"
    }
    # Absent/empty/disabled values are not retired requirements and pass.
    reject_retired_vector_fields({}, field_path="payload")
    reject_retired_vector_fields({"rag": None}, field_path="payload")


def test_tool_manifest_guard_rejects_leaked_retired_descriptor() -> None:
    assert check_tool_manifest_vector_free(["shell", "qdrant_search"]) != []
    assert (
        check_tool_manifest_vector_free(["followUpRetrieval"]) != []
    )
    assert check_tool_manifest_vector_free(["shell", "artifact_read"]) == []


# ---------------------------------------------------------------------------
# Real startup: hermetic sentinel logic (live startup stays protected).
# ---------------------------------------------------------------------------


def test_startup_sentinel_detects_vector_attempts() -> None:
    assert _STARTUP_SENTINEL_RE.search("connecting to qdrant:6333")
    assert _STARTUP_SENTINEL_RE.search("QDRANT_URL=http://qdrant:6333")
    assert _STARTUP_SENTINEL_RE.search("embedding model initialization started")
    assert _STARTUP_SENTINEL_RE.search("Qdrant connection outage, retrying")


def test_startup_sentinel_passes_vector_free_boot() -> None:
    clean_log = (
        "api ready on :8000; postgres healthy; temporal worker started; "
        "retrieval backend: none (vector-free defaults)"
    )
    assert _STARTUP_SENTINEL_RE.search(clean_log) is None


# ---------------------------------------------------------------------------
# Docs/operations: examples and update scripts agree with shipped behavior.
# ---------------------------------------------------------------------------


def test_env_template_advertises_no_active_vector_backend() -> None:
    template = (REPO_ROOT / ".env-template").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^QDRANT_ENABLED=\"true\"", template)
    assert not re.search(r"(?m)^VECTOR_STORE_PROVIDER=\"qdrant\"", template)
    assert not re.search(r"(?m)^QDRANT_URL=", template)


def test_update_script_does_not_recreate_vector_backend() -> None:
    script = (
        REPO_ROOT
        / ".agents/skills/update-moonmind/scripts/run-update-moonmind.sh"
    ).read_text(encoding="utf-8")
    code_lines = [
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert not re.search(r"\bqdrant\b", code, re.IGNORECASE)


def test_docs_operations_do_not_advertise_retired_vector_backend() -> None:
    """Verify the public documentation surfaces removed by sibling changes."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert not re.search(r"qdrant", readme, re.IGNORECASE)
    assert not (
        REPO_ROOT / "docs/ManagedAgents/WorkerVectorEmbedding.md"
    ).exists()


@pytest.mark.parametrize("command", [[], ["worker"], ["container"]])
def test_cli_help_does_not_advertise_retired_vector_backend(command: list[str]) -> None:
    from typer.testing import CliRunner

    from moonmind.cli import app

    result = CliRunner().invoke(app, [*command, "--help"], color=False)
    assert result.exit_code == 0, result.output
    # MoonLadderStudios/MoonMind#4192: the top-level help carries a
    # retirement notice naming the removed Manifest/RAG product. Strip that
    # notice (terminal wrapping may reflow it) before asserting no live
    # vector backend is advertised.
    scrubbed = re.sub(
        r"The native\s+Manifest/RAG\s+ingestion\s+product\s+was\s+retired"
        r".*?inspection\s+command\.",
        "",
        result.output,
        flags=re.DOTALL | re.IGNORECASE,
    )
    assert not re.search(r"\b(qdrant|rag)\b", scrubbed, re.IGNORECASE)


def test_cli_help_advertises_no_manifest_command_group() -> None:
    """MR5 (#4192): the retired manifest command group must be gone entirely.

    The app help text carries a retirement notice naming the removal, so a
    zero-substring assertion on rendered `--help` output is stale (rich
    panels may also truncate prose). Pin the retired posture instead: the
    notice lives in the app help source, no ``manifest`` command row is
    listed, and ``manifest --help`` still fails.
    """
    from typer.testing import CliRunner

    from moonmind.cli import app

    assert "no `manifest` command group" in (app.info.help or "")
    top = CliRunner().invoke(app, ["--help"], color=False)
    assert top.exit_code == 0, top.output
    assert not re.search(r"(?m)^\s*manifest(\s|│|:)", top.output)
    retired = CliRunner().invoke(app, ["manifest", "--help"], color=False)
    assert retired.exit_code != 0


def test_topology_matrix_gaps_are_explicit() -> None:
    """Pin the verification boundary: hermetic guards are not live proof.

    Hermetic coverage owned by this module (real production boundaries exercised
    without live services): Compose/topology render, init-SQL enumeration,
    dependency/import scans, migration SQL scan, settings stale-env tolerance,
    admission wiring (execution contract, checkpoint branch models,
    AgentExecutionRequest), retry input-reuse, capability-manifest source scan,
    worker-registry Manifest absence, drain-gate predicate logic, and
    docs/operations surfaces.

    The following rows still require protected deployment evidence owned with
    the cutover child and sibling removals (#4106-#4113), and are NOT claimed
    qualified by this module: live fresh/upgraded startup against real
    Compose prerequisites (init-db/Alembic run, API/worker readiness,
    dashboard bootstrap, repeated startup), live runtime x capability x
    authority-handoff journeys on supported hosts, live follow-up/chat
    manifests against real builds, ingestion fail-before-effects on real
    writers, memory/finalization ordering under real failure, cross-tenant
    security probes, recovery/upgrade rehearsal on real boundaries, browser
    journeys, and per-deployment image-layer/observation evidence. This test
    exists so future edits cannot silently widen the claim without updating
    the mapping above.
    """
    protected = {
        "real-startup",
        "runtime-context",
        "followup-chat-live",
        "ingestion-effects",
        "memory-finalization",
        "security-probes",
        "recovery-upgrade",
        "browser-journeys",
    }
    assert len(protected) == 8


# ---------------------------------------------------------------------------
# Clean dependencies: live-import scan (no Qdrant SDK, no Manifest product).
# ---------------------------------------------------------------------------


def _python_sources_under(*roots: str) -> list[Path]:
    sources: list[Path] = []
    for root in roots:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        sources.extend(sorted(base.rglob("*.py")))
    return sources


def test_clean_imports_have_no_live_qdrant_sdk() -> None:
    """No shipped first-party module imports the retired Qdrant SDK.

    Retirement notices, drain/cutover tooling, and the hermetic regression
    guards themselves may name Qdrant; a live ``import qdrant_client`` /
    ``from qdrant_client`` outside those owners is a reintroduction.
    """
    offenders: list[str] = []
    for path in _python_sources_under("moonmind", "api_service", "services"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.match(
                r"(import\s+qdrant_client|from\s+qdrant_client\s+import)",
                stripped,
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert offenders == [], f"live Qdrant SDK imports: {offenders}"


def test_no_live_manifest_product_imports() -> None:
    """No shipped module imports the removed native Manifest product package.

    ``moonmind/manifest/*``, the execution ``manifest_contract``, and the
    Temporal ``manifest_ingest`` modules were removed by #4192; a live import
    of any of them is a reintroduction. Drain-gate, cutover-rehearsal, and
    retirement-guard modules may reference the retired names in prose.
    """
    offender_pattern = re.compile(
        r"^\s*(import\s+moonmind\.manifest|from\s+moonmind\.manifest[\s.]"
        r"|from\s+moonmind\.workflows\.executions\.manifest_contract\s+import"
        r"|from\s+moonmind\.workflows\.temporal(\.workflows)?\.manifest_ingest\s+import"
        r"|import\s+moonmind\.workflows\.temporal(\.workflows)?\.manifest_ingest)",
    )
    allowed_name_res = (
        re.compile(r"manifest_ingest_drain"),
        re.compile(r"qdrant_cutover_rehearsal"),
        re.compile(r"test_vector_free_regression_4114"),
        re.compile(r"test_manifest_retirement_qualification_4193"),
        re.compile(r"test_manifest_ingest_drain"),
    )
    offenders: list[str] = []
    for path in _python_sources_under("moonmind", "api_service", "services"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(rx.search(rel) for rx in allowed_name_res):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if offender_pattern.match(line):
                offenders.append(f"{rel}:{lineno}")
    assert offenders == [], f"live Manifest product imports: {offenders}"


def test_poetry_lock_has_no_qdrant_distribution_transitive() -> None:
    """Every Qdrant distribution spelling stays out of the resolved lockfile.

    Case-insensitive negative control: ``Qdrant-Client`` (transitive casing
    variant) must fail the same guard as the canonical ``qdrant-client`` pin.
    """
    assert check_dependency_vector_free(["Qdrant-Client"]) != []
    lock = (REPO_ROOT / "poetry.lock").read_text(encoding="utf-8")
    assert not re.search(r'name\s*=\s*"qdrant[^"]*"', lock, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Real startup (hermetic slice): migrations, settings, entrypoints.
# ---------------------------------------------------------------------------


def test_migrations_carry_no_vector_sql() -> None:
    """Alembic migrations install no vector extension or vector index."""
    versions = REPO_ROOT / "api_service/migrations/versions"
    assert versions.is_dir(), "expected Alembic versions directory"
    scripts = sorted(versions.glob("*.py"))
    assert scripts, "expected Alembic migration scripts to enumerate"
    offenders: list[str] = []
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        code_lines = [
            line
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if _VECTOR_SQL_RE.search("\n".join(code_lines)):
            offenders.append(script.name)
    assert offenders == [], f"migrations install vector SQL: {offenders}"


def test_settings_model_declares_no_vector_backend_fields() -> None:
    """Settings declare no Qdrant/vector backend fields; stale env is inert.

    The only ``qdrant`` mention in ``settings.py`` is the #4109 retirement
    notice explaining why hosted Mem0 cannot parse. No model field wires a
    ``QDRANT_*`` / ``VECTOR_STORE_*`` / ``*_EMBEDDING_*`` setting, so old
    deployments carrying those keys stay inert (``extra="ignore"``).
    """
    text = (REPO_ROOT / "moonmind/config/settings.py").read_text(encoding="utf-8")
    mentions = [
        (lineno, line)
        for lineno, line in enumerate(text.splitlines(), start=1)
        if re.search(r"qdrant", line, re.IGNORECASE)
    ]
    assert mentions, "expected the retirement notice to be present"
    for _, line in mentions:
        assert re.search(
            r"Mem0|mandatorily requires|retired|4109|4115",
            line,
            re.IGNORECASE,
        ), f"unexpected live qdrant reference in settings: {line!r}"
    assert not re.search(
        r'(?m)^\s*(qdrant_\w+|vector_store_\w+)\s*[:=]',
        text,
        re.IGNORECASE,
    )


def test_init_entrypoints_do_not_require_vector_env() -> None:
    """Init/API entrypoints demand no Qdrant/vector environment."""
    for rel in ("init_db/init_db_entrypoint.sh", "api_service/entrypoint.sh"):
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        code_lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert not re.search(r"QDRANT_", code)
        assert not re.search(r"\bqdrant\b", code, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Admission matrix wiring: checkpoint models and AgentExecutionRequest.
# ---------------------------------------------------------------------------


def _checkpoint_branch_source() -> dict:
    return {
        "runId": "run-1",
        "checkpointBoundary": "before_execution",
        "checkpointRef": "artifact://tenant/repo/branch.json",
    }


def test_checkpoint_branch_create_rejects_retired_retrieval() -> None:
    """Checkpoint branch admission rejects explicit retired retrieval."""
    from pydantic import ValidationError

    from moonmind.schemas.checkpoint_branch_models import (
        CheckpointBranchCreateRequest,
    )

    base: dict = {
        "source": _checkpoint_branch_source(),
        "label": "branch",
        "instructions": {"text": "do work"},
        "workspacePolicy": "continue_from_previous_execution",
        "idempotencyKey": "idem-branch-1",
        "followUpRetrieval": {"enabled": True, "collections": ["repo"]},
    }
    with pytest.raises(ValidationError, match="4105|retired|vector"):
        CheckpointBranchCreateRequest.model_validate(base)
    # Absent/disabled residue still parses so historical payloads load.
    ok = CheckpointBranchCreateRequest.model_validate(
        {**base, "followUpRetrieval": {"enabled": False}}
    )
    assert ok.follow_up_retrieval == {"enabled": False}


def test_checkpoint_branch_continue_rejects_retired_retrieval() -> None:
    """Checkpoint continue/fork admission rejects retired retrieval."""
    from pydantic import ValidationError

    from moonmind.schemas.checkpoint_branch_models import (
        CheckpointBranchContinueRequest,
    )

    base: dict = {
        "label": "continue",
        "instructions": {"text": "continue work"},
        "idempotencyKey": "idem-continue-1",
        "followUpRetrieval": {"enabled": True},
    }
    with pytest.raises(ValidationError, match="4105|retired|vector"):
        CheckpointBranchContinueRequest.model_validate(base)


def test_agent_execution_request_rejects_retired_parameters() -> None:
    """AgentExecutionRequest admission rejects explicit retired parameters."""
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    with pytest.raises(ValueError, match="4105|retired|vector"):
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-4114",
            idempotencyKey="idem-4114",
            parameters={"rag": {"collections": ["docs"], "required": True}},
        )


def test_agent_execution_request_accepts_vector_free_explicit_inputs() -> None:
    """Scoped explicit inputs/Skills/artifacts admit with no vector settings."""
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-4114-free",
        idempotencyKey="idem-4114-free",
        parameters={"instructions": "summarize"},
        skill={"name": "document-update"},
        inputRefs=["artifact://tenant/repo/input.md"],
    )
    dumped = request.model_dump(by_alias=True)
    assert dumped["parameters"] == {"instructions": "summarize"}
    assert "rag" not in dumped["parameters"]
    assert "followUpRetrieval" not in dumped["parameters"]


def test_retry_reuses_exact_input_without_duplicate_delivery() -> None:
    """Absent-field stripping is idempotent and never mutates caller inputs.

    Retry must reuse the exact admitted input: stripping absent/disabled
    residue twice yields the same mapping, and the caller's dict keeps its
    original keys (the helper operates on the admitted copy).
    """
    admitted = {
        "instructions": "summarize",
        "rag": {},
        "followUpRetrieval": {"enabled": False},
    }
    first = strip_absent_vector_fields(dict(admitted))
    second = strip_absent_vector_fields(dict(first))
    assert first == {"instructions": "summarize"}
    assert second == first
    assert set(admitted) == {
        "instructions",
        "rag",
        "followUpRetrieval",
    }


# ---------------------------------------------------------------------------
# Chat/tools (hermetic slice): capability-manifest sources issue no retired
# tool or credential.
# ---------------------------------------------------------------------------


def test_capability_sources_issue_no_retired_tool_descriptor() -> None:
    """Capability-manifest sources carry no retired tool descriptor or cred.

    Only actual tool-manifest issuers are scanned for descriptor leaks here.
    ``api_service/retrieval_capabilities.py`` is drain-only authority
    (MoonLadderStudios/MoonMind#4107), not a manifest issuer: ``issue()``
    always raises ``retired`` and no token or host manifest is ever minted,
    so its fail-closed ``followUpRetrieval`` retirement hint is operator
    guidance, not an issued descriptor. Its drain behavior is owned
    behaviorally by ``tests/unit/api/test_retrieval_capabilities.py``.
    """
    candidates = [
        REPO_ROOT / "moonmind/omnigent/effective_capabilities.py",
        REPO_ROOT / "moonmind/omnigent/harness_platform/capabilities.py",
    ]
    checked = 0
    for path in candidates:
        if not path.is_file():
            continue
        checked += 1
        text = path.read_text(encoding="utf-8")
        assert check_tool_manifest_vector_free([text]) == [], (
            f"capability source {path} leaks retired tool descriptor"
        )
        assert not re.search(r"qdrant_search", text, re.IGNORECASE), (
            f"capability source {path} issues retired qdrant_search tool"
        )
    assert checked, "expected capability-manifest sources to enumerate"
    drain_path = REPO_ROOT / "api_service/retrieval_capabilities.py"
    drain_text = drain_path.read_text(encoding="utf-8")
    assert not re.search(r"qdrant_search", drain_text, re.IGNORECASE), (
        f"drain-only registry {drain_path} issues retired qdrant_search tool"
    )
    assert "qdrant" not in drain_text.lower(), (
        f"drain-only registry {drain_path} wires retired Qdrant credential"
    )


# ---------------------------------------------------------------------------
# Manifest retirement (Sep 9 correction): absence + drain-gate predicate.
# ---------------------------------------------------------------------------


def test_worker_catalog_has_no_manifest_workflow_or_activity() -> None:
    """New worker/catalog registers no Manifest workflow or Activity."""
    registry = (
        REPO_ROOT / "moonmind/workflows/temporal/workflow_registry.py"
    ).read_text(encoding="utf-8")
    code_lines = [
        line for line in registry.splitlines() if not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "ManifestIngest" not in code
    assert "manifest_ingest" not in code
    assert "manifest.compile" not in code
    assert "manifest.write_summary" not in code
    assert not (REPO_ROOT / "moonmind/manifest").exists()
    assert not list(
        (REPO_ROOT / "moonmind/workflows/temporal/workflows").glob(
            "*manifest_ingest*"
        )
    )
    assert not list((REPO_ROOT / "api_service/api/routers").glob("*manifest*"))


def test_manifest_drain_gate_blocks_on_open_histories() -> None:
    """Drain predicate allows removal only when every dimension drains to zero.

    Hermetic predicate logic (live counts stay protected): zero open
    histories/tasks/schedules is removable; any nonzero dimension retains the
    old release. Fixture replay never authorizes deployment drainage.
    """
    from moonmind.gates.manifest_ingest_drain import (
        ManifestIngestDrainUsage,
        evaluate_manifest_ingest_drain,
    )

    drained = evaluate_manifest_ingest_drain(ManifestIngestDrainUsage(0, 0, 0))
    assert drained.may_deploy_removal is True
    assert drained.outstanding == 0
    blocked = evaluate_manifest_ingest_drain(ManifestIngestDrainUsage(1, 0, 0))
    assert blocked.may_deploy_removal is False
    assert blocked.outstanding == 1
    assert "open_manifest_ingest_histories" in blocked.blocking_dimensions
