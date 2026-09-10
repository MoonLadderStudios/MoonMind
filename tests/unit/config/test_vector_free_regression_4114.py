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
    assert not re.search(r"\b(qdrant|rag)\b", result.output, re.IGNORECASE)


def test_cli_help_advertises_no_manifest_command_group() -> None:
    """MR5 (#4192): the retired manifest command group must be gone entirely."""
    from typer.testing import CliRunner

    from moonmind.cli import app

    top = CliRunner().invoke(app, ["--help"], color=False)
    assert top.exit_code == 0, top.output
    assert "manifest" not in top.output.lower()
    retired = CliRunner().invoke(app, ["manifest", "--help"], color=False)
    assert retired.exit_code != 0


def test_topology_matrix_gaps_are_explicit() -> None:
    """Pin the verification boundary: hermetic guards are not live proof.

    The following rows require protected deployment evidence owned with the
    cutover child and sibling removals (#4106-#4113), and are NOT claimed
    qualified by this module: real startup (fresh Compose prerequisites,
    init-db/Alembic, API/worker readiness, dashboard bootstrap, repeated
    startup), runtime x capability x authority-handoff journeys,
    follow-up/chat manifests against live builds, ingestion fail-before-
    effects on real writers, memory/finalization ordering under failure,
    cross-tenant security probes, recovery/upgrade rehearsal, and browser
    journeys. This test exists so future edits cannot silently widen the
    claim without updating the mapping above.
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
