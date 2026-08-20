"""Pin the exact-artifact runtime probe's endpoints to the real application.

Source issue: MoonLadderStudios/MoonMind#3710.

``tools/_exact_artifact_runtime_probes.py`` is the CI-only probe layer that
exercises the *running* exact image; it is deliberately excluded from the
runtime unit suite because it needs a container.  That exclusion is exactly how
a probe can point at an endpoint that does not exist and silently mis-gate a
healthy image (the prior verify found ``/health`` instead of ``/healthz`` and
non-existent SSE/WS paths).

This hermetic test closes that gap: it asserts that every path the probe hits
(liveness, HTTP, Omnigent SSE, Omnigent WebSocket, worker readiness) resolves
through a real handler / payload key in the current application, so a future
route rename fails a unit test instead of the Tier-1 job on a valid image.
"""

from __future__ import annotations

import json

import pytest

from tools._exact_artifact_runtime_probes import (
    probe_route_templates,
    _resolve_probe_path,
)


@pytest.fixture(scope="module")
def templates() -> dict[str, str]:
    return probe_route_templates()


def _bridge_route_paths() -> set[str]:
    """Full production paths registered on the Omnigent bridge router.

    ``api_service.main`` includes this exact router object at
    ``OMNIGENT_BRIDGE_MOUNT_PATH``; asserting against the router's own routes is
    faithful to the mounted surface and independent of app-construction order.
    """

    from api_service.api.routers.omnigent_bridge import (
        router as bridge_router,
        OMNIGENT_BRIDGE_MOUNT_PATH,
    )

    mount = OMNIGENT_BRIDGE_MOUNT_PATH.rstrip("/")
    return {
        mount + route.path
        for route in bridge_router.routes
        if getattr(route, "path", None)
    }


def test_liveness_probe_targets_the_real_healthz_route(templates: dict[str, str]) -> None:
    import api_service.main as main_module

    assert templates["liveness"] == "/healthz"
    health_paths = {
        getattr(route, "path", None) for route in main_module.health_router.routes
    }
    assert templates["liveness"] in health_paths
    # The prior defect polled /health, which is not a registered route.
    assert "/health" not in health_paths


def test_http_probe_targets_the_app_openapi_route(templates: dict[str, str]) -> None:
    import api_service.main as main_module

    assert templates["http"] == main_module.app.openapi_url


def test_sse_probe_targets_a_registered_omnigent_stream_route(
    templates: dict[str, str],
) -> None:
    bridge_paths = _bridge_route_paths()
    assert templates["sse"] in bridge_paths
    # The prior defect probed /api/omnigent/live/events, which does not exist.
    assert "/api/omnigent/live/events" not in bridge_paths


def test_websocket_probe_targets_a_registered_omnigent_tunnel_route(
    templates: dict[str, str],
) -> None:
    from starlette.routing import WebSocketRoute

    from api_service.api.routers.omnigent_bridge import (
        router as bridge_router,
        OMNIGENT_BRIDGE_MOUNT_PATH,
    )

    mount = OMNIGENT_BRIDGE_MOUNT_PATH.rstrip("/")
    websocket_paths = {
        mount + route.path
        for route in bridge_router.routes
        if isinstance(route, WebSocketRoute) and getattr(route, "path", None)
    }
    assert templates["websocket"] in websocket_paths
    # The prior defect probed /ws/omnigent, which is not a registered route and
    # would fall through to HTTP 404 (the #3697 failure mode).
    assert "/api/omnigent/ws/omnigent" not in websocket_paths


def test_worker_readiness_probe_targets_the_real_readyz_key(
    templates: dict[str, str],
) -> None:
    from moonmind.workflows.temporal.worker_healthcheck import (
        WorkerHealthState,
        _build_response_body,
    )

    assert templates["worker_ready"] == "/readyz"
    state = WorkerHealthState(
        temporal_connected=True,
        workers_constructed=True,
        pollers_started=True,
        readiness_metadata={"taskQueues": ["agent-runtime"]},
    )
    body = json.loads(_build_response_body(state, readiness=True))
    # The worker probe reads exactly these keys off the /readyz payload.
    assert body["ready"] is True
    assert body["taskQueues"] == ["agent-runtime"]


def test_resolve_probe_path_fills_placeholders(templates: dict[str, str]) -> None:
    for key in ("sse", "websocket"):
        resolved = _resolve_probe_path(templates[key])
        assert "{" not in resolved and "}" not in resolved
    # Paths without placeholders are returned unchanged.
    assert _resolve_probe_path(templates["liveness"]) == "/healthz"


def test_a_renamed_route_would_break_the_probe_contract() -> None:
    """Guard: a bogus probe path is absent from the real surface, proving the
    contract test actually catches drift rather than trivially passing."""

    bridge_paths = _bridge_route_paths()
    assert "/api/omnigent/v1/sessions/{session_id}/renamed-stream" not in bridge_paths


# --- Container-boundary contracts (no Docker required) -------------------------


def test_postgres_env_translates_the_settings_the_image_actually_reads() -> None:
    """The image resolves its database from POSTGRES_*, not DATABASE_URL.

    Passing only ``DATABASE_URL`` left every probe container pointed at the
    Compose default host, so probes failed for a reason unrelated to the
    artifact under test.
    """
    from tools._exact_artifact_runtime_probes import postgres_env

    env = postgres_env("postgresql://moonmind:sekret@127.0.0.1:5432/moonmind_probe")

    assert env == {
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "moonmind_probe",
        "POSTGRES_USER": "moonmind",
        "POSTGRES_PASSWORD": "sekret",
    }


def test_postgres_env_rejects_a_url_without_a_database() -> None:
    from tools._exact_artifact_runtime_probes import postgres_env

    with pytest.raises(ValueError):
        postgres_env("postgresql://moonmind@127.0.0.1:5432")


def test_database_url_for_switches_only_the_database_name() -> None:
    from tools._exact_artifact_runtime_probes import database_url_for

    assert (
        database_url_for("postgresql://u:p@127.0.0.1:5432/postgres", "probe_clean")
        == "postgresql://u:p@127.0.0.1:5432/probe_clean"
    )


def test_probe_assets_come_from_a_mount_and_imports_from_the_image() -> None:
    """The deployable image must not carry probe assets.

    ``tools/`` is not copied into the production image, so the harness is
    supplied through an explicit read-only mount. ``PYTHONPATH`` names only the
    image's application root, so the mount can never shadow the artifact under
    test.
    """
    from tools.run_omnigent_exact_artifact_conformance import (
        APP_ROOT,
        PROBE_MOUNT,
        REPO_ROOT,
        in_image_probe_command,
    )

    command = in_image_probe_command("sha256:" + "a" * 64, "server")

    assert f"{REPO_ROOT}:{PROBE_MOUNT}:ro" in command
    assert f"PYTHONPATH={APP_ROOT}" in command
    assert f"{PROBE_MOUNT}/tools/omnigent_exact_artifact_probe.py" in command
    # The image reference is the locally resolvable content id; a repo digest for
    # a locally loaded image is unpullable.
    assert "sha256:" + "a" * 64 in command
    assert not any(part.startswith("moonmind-exact-artifact@") for part in command)


def test_alembic_probe_names_the_repository_configuration() -> None:
    """Bare ``alembic`` cannot find a configuration in the image's /app."""
    from tools import _exact_artifact_runtime_probes as probes

    command = probes._alembic("sha256:" + "b" * 64, {"POSTGRES_DB": "x"}, "upgrade", "head")

    assert "-c" in command
    assert probes.ALEMBIC_CONFIG in command
    assert command.index("-c") < command.index(probes.ALEMBIC_CONFIG)
    assert probes.ALEMBIC_CONFIG == "api_service/migrations/alembic.ini"


def test_migration_probes_use_separate_databases(monkeypatch) -> None:
    """Each migration scenario needs its own empty database.

    Reusing one database makes the prior-schema scenario meaningless: an upgrade
    replayed over tables that already exist fails for a valid migration chain.
    """
    from tools import _exact_artifact_runtime_probes as probes

    created: list[str] = []

    def fake_reset(admin_url: str, name: str) -> str:
        created.append(name)
        return probes.database_url_for(admin_url, name)

    scenarios: list[tuple[str, bool]] = []

    def fake_probe_migrations(image, database_url, *, prior_schema):
        scenarios.append((database_url, prior_schema))
        return True, "ok"

    monkeypatch.setattr(probes, "reset_database", fake_reset)
    monkeypatch.setattr(probes, "_probe_migrations", fake_probe_migrations)
    monkeypatch.setattr(
        probes,
        "_probe_server_and_ui",
        lambda *a, **k: ([], []),
    )
    monkeypatch.setattr(probes, "_probe_worker", lambda *a, **k: [])

    server, _worker, _ui = probes.gather_from_image(
        image="sha256:" + "c" * 64,
        database_url="postgresql://u:p@127.0.0.1:5432/postgres",
        temporal_address="127.0.0.1:7233",
        ui_probe=lambda _base_url: [],
    )

    assert created == [
        "moonmind_exact_artifact_clean",
        "moonmind_exact_artifact_prior",
    ]
    clean_url, prior_url = (url for url, _ in scenarios)
    assert clean_url != prior_url
    assert [prior for _, prior in scenarios] == [False, True]
    assert {signal["name"] for signal in server} == {
        "migrations_clean_apply",
        "migrations_prior_schema_upgrade",
    }


def test_prior_schema_probe_materializes_a_real_prior_revision(monkeypatch) -> None:
    """``alembic stamp base`` only moves the version marker; upgrade from a real
    parent revision is what a deployment onto an existing database does."""
    from tools import _exact_artifact_runtime_probes as probes

    invocations: list[tuple[str, ...]] = []

    class _Completed:
        returncode = 0
        stdout = "Revision ID: head-rev\nParent: parent-rev\n"
        stderr = ""

    def fake_run(args, *, timeout=120):
        invocations.append(tuple(args[args.index("-c") + 2 :]))
        return _Completed()

    monkeypatch.setattr(probes, "_run", fake_run)

    ok, detail = probes._probe_migrations(
        "sha256:" + "d" * 64,
        "postgresql://u:p@127.0.0.1:5432/probe_prior",
        prior_schema=True,
    )

    assert ok, detail
    assert ("show", "head") in invocations
    assert ("upgrade", "parent-rev") in invocations
    assert ("upgrade", "head") in invocations
    # A stamped base would not materialize any schema.
    assert not any("stamp" in inv for inv in invocations)


def test_prior_schema_probe_fails_closed_without_a_parent_revision(monkeypatch) -> None:
    from tools import _exact_artifact_runtime_probes as probes

    class _Completed:
        returncode = 0
        stdout = "Revision ID: only-rev\nParent: \n"
        stderr = ""

    monkeypatch.setattr(probes, "_run", lambda *a, **k: _Completed())

    ok, detail = probes._probe_migrations(
        "sha256:" + "e" * 64,
        "postgresql://u:p@127.0.0.1:5432/probe_prior",
        prior_schema=True,
    )

    assert ok is False
    assert "preceding" in detail or "parent" in detail


def test_worker_probe_requires_a_reachable_temporal_address(monkeypatch) -> None:
    """The worker connects to Temporal before advertising readiness."""
    from tools import _exact_artifact_runtime_probes as probes

    captured: dict[str, str] = {}

    class _Ctx:
        def __enter__(self):
            return "container"

        def __exit__(self, *_exc):
            return False

    def fake_container(image, *, command=None, env=None, repo_dir=None):
        captured.update(env or {})
        return _Ctx()

    monkeypatch.setattr(probes, "_container", fake_container)
    monkeypatch.setattr(probes.time, "sleep", lambda _s: None)

    probes._probe_worker(
        "sha256:" + "f" * 64,
        database_url="postgresql://u:p@127.0.0.1:5432/probe_clean",
        temporal_address="127.0.0.1:7233",
    )

    assert captured["TEMPORAL_ADDRESS"] == "127.0.0.1:7233"
    assert captured["POSTGRES_HOST"] == "127.0.0.1"


def test_hosted_ui_capture_interpretation_fails_closed() -> None:
    from tools._exact_artifact_runtime_probes import interpret_hosted_ui_capture

    passed = {s["name"]: s for s in interpret_hosted_ui_capture(0, "hosted UI capture ok")}
    assert passed["hosted_bootstrap_consumed"]["ok"] is True
    assert passed["no_root_v1_requests"]["ok"] is True

    failed = {
        s["name"]: s
        for s in interpret_hosted_ui_capture(1, "root-v1-request observed: /v1/x")
    }
    assert failed["hosted_bootstrap_consumed"]["ok"] is False
    assert failed["no_root_v1_requests"]["ok"] is False


def test_restart_capability_is_required_by_the_gate() -> None:
    """The server probe emits the restart signal the gate requires."""
    from moonmind.omnigent.exact_artifact_conformance import (
        REQUIRED_SERVER_CAPABILITIES,
    )

    assert "api_restart_against_existing_schema" in REQUIRED_SERVER_CAPABILITIES


def test_ui_capture_runs_while_the_api_container_is_still_alive(monkeypatch) -> None:
    """The hosted UI is served by the API container under test.

    Completing and removing the API container before starting the browser
    capture would leave nothing listening at the hosted origin, so the UI
    capabilities could never pass.
    """
    from tools import _exact_artifact_runtime_probes as probes

    events: list[str] = []

    class _Ctx:
        def __enter__(self):
            events.append("container_started")
            return "api-container"

        def __exit__(self, *_exc):
            events.append("container_removed")
            return False

    monkeypatch.setattr(
        probes, "_container", lambda *a, **k: _Ctx()
    )
    monkeypatch.setattr(probes, "_await_health", lambda *a, **k: True)
    monkeypatch.setattr(probes, "_probe_server_routes", lambda _base: [])
    monkeypatch.setattr(
        probes,
        "_restart_container",
        lambda *a, **k: (True, "restarted"),
    )

    def ui_probe(base_url: str):
        events.append("ui_capture")
        assert base_url == f"http://127.0.0.1:{probes.API_PORT}"
        return [{"name": "hosted_bootstrap_consumed", "ok": True, "detail": "ok"}]

    server, ui = probes._probe_server_and_ui(
        "sha256:" + "1" * 64,
        database_url="postgresql://u:p@127.0.0.1:5432/probe_clean",
        repo_dir=probes.REPO_ROOT,
        ui_probe=ui_probe,
    )

    assert events == ["container_started", "ui_capture", "container_removed"]
    assert ui[0]["name"] == "hosted_bootstrap_consumed"
    assert any(
        signal["name"] == "api_restart_against_existing_schema" and signal["ok"]
        for signal in server
    )


def test_ui_capture_fails_closed_when_the_api_never_starts(monkeypatch) -> None:
    from tools import _exact_artifact_runtime_probes as probes

    class _Ctx:
        def __enter__(self):
            return "api-container"

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(probes, "_container", lambda *a, **k: _Ctx())
    monkeypatch.setattr(probes, "_await_health", lambda *a, **k: False)

    def ui_probe(_base_url):  # pragma: no cover - must not be called
        raise AssertionError("no hosted origin exists to capture")

    server, ui = probes._probe_server_and_ui(
        "sha256:" + "2" * 64,
        database_url="postgresql://u:p@127.0.0.1:5432/probe_clean",
        repo_dir=probes.REPO_ROOT,
        ui_probe=ui_probe,
    )

    assert all(signal["ok"] is False for signal in server)
    assert {signal["name"] for signal in ui} == {
        "hosted_bootstrap_consumed",
        "no_root_v1_requests",
    }
    assert all(signal["ok"] is False for signal in ui)
