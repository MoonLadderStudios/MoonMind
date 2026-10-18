from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from api_service.services import omnigent_execution_plan_service as service
from moonmind.omnigent.harness_platform.catalog import TrustState
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _compile_opencode_plan,
    _ready_opencode_image_pair,  # noqa: F401 - pytest discovers the autouse fixture.
)


@pytest_asyncio.fixture
async def stock_harness_http_inventory():
    """Serve the stock harness wire contract over a real loopback connection."""

    from aiohttp import web

    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    # harness_plugins.harness_catalog() emits picker rows, not native wrappers;
    # harness_capabilities.HarnessCapabilities.as_dict() supplies the auth enum.
    harnesses = [
        {
            "id": harness_id,
            "label": harness_id,
            "capabilities": {"integration_mode": integration, "auth": auth},
        }
        for harness_id, integration, auth in (
            ("codex", "cli-subprocess", "omnigent-credential"),
            ("claude-sdk", "sdk-in-process", "omnigent-credential"),
            ("cursor", "sdk-in-process", "own-auth"),
        )
    ]
    responses = {
        "/api/version": {"version": "0.16.0"},
        "/v1/harnesses": {"data": harnesses},
        "/v1/agents": {
            "data": [
                {
                    "id": "stock-codex-native-agent",
                    "name": "codex-native-ui",
                    "harness": "codex-native",
                    "builtin": True,
                }
            ],
            "has_more": False,
        },
        "/v1/hosts": {"hosts": []},
        "/api/agents/other": {"capabilities": {"auth": "own-auth"}},
    }
    requests = []

    async def respond(request):
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer synthetic-catalog-token"
        requests.append(request.path)
        return web.json_response(responses[request.path])

    app = web.Application()
    app.router.add_get("/{path:.*}", respond)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield SimpleNamespace(
            client=OmnigentHttpClient(
                base_url=f"http://127.0.0.1:{runner.addresses[0][1]}",
                api_token="synthetic-catalog-token",
            ),
            responses=responses,
            requests=requests,
        )
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.component
async def test_stock_harness_http_auth_survives_catalog_and_plan_compilation(
    monkeypatch, tmp_path, stock_harness_http_inventory
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api_service.db.models import Base
    from api_service.services.omnigent_agent_profile_service import (
        _overlay_native_harnesses,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        DbHarnessCatalogRepository,
        OmnigentHarnessCatalogService,
    )
    from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore

    inventory = stock_harness_http_inventory
    # These are synthetic credential values, not public capability metadata.
    secret = "synthetic-private-catalog-credential"
    for row in inventory.responses["/v1/harnesses"]["data"]:
        row["auth"] = secret
        row["capabilities"]["api_token"] = secret
        row["runtime_requirements"] = {"credentials": {"token": secret}}
    parsed_rows = await inventory.client.list_harnesses()
    assert secret not in json.dumps(parsed_rows)
    assert all(row["auth"] == "[REDACTED]" for row in parsed_rows)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/http-catalog.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalogs = DbHarnessCatalogRepository(sessions)
        synchronized = await OmnigentHarnessCatalogService(
            client=inventory.client,
            repository=catalogs,
            endpoint_ref="default",
            omnigent_build_digest="sha256:" + "6" * 64,
            observation_overlay=_overlay_native_harnesses,
        ).synchronize()
        auth_models = {
            row.id: row.capabilities.authModel
            for row in synchronized.snapshot.harnesses
        }
        assert auth_models == {
            "codex": "omnigent-credential",
            "claude-sdk": "omnigent-credential",
            "cursor": "own-auth",
            "codex-native": "oauth_volume",
            "opencode-native": "own-auth",
        }
        persisted = await catalogs.load(synchronized.snapshot.catalogRef)
        assert persisted is not None
        assert persisted.snapshot == synchronized.snapshot
        assert {
            row.implementationRef: row.trustState for row in persisted.trust_records
        } == {
            row.implementationRef: row.trustState for row in synchronized.trust_records
        }
        assert secret not in persisted.snapshot.model_dump_json()
        assert secret not in json.dumps(persisted.diagnostics)
        # The exception is specific to the catalog endpoint and capability field.
        other = await inventory.client.get_agent("other")
        assert other["capabilities"]["auth"] == "[REDACTED]"

        monkeypatch.setattr(
            service,
            "resolve_execution_evidence",
            lambda *_args, **_kwargs: (None, "uncertified"),
        )
        plans = DbExecutionPlanStore(sessions)
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref="opencode-on-demand@1",
            plan_store=plans,
            session_factory=sessions,
        )
        plan = compiled.envelope.payload
        assert plan.harnessCatalogRef == synchronized.snapshot.catalogRef
        assert plan.harnessId == "opencode-native"
        assert plan.executionRealizerRef == "generic-omnigent-host@1"
        assert plan.credentialBindings["primary-model"].materializerRef == (
            "opencode-auth-json@1"
        )
        assert await plans.load(compiled.envelope.planRef) == compiled.envelope
        assert secret not in compiled.envelope.model_dump_json()
        assert inventory.requests == [
            "/v1/harnesses",
            "/api/version",
            "/v1/harnesses",
            "/v1/agents",
            "/v1/hosts",
            "/api/agents/other",
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.component
@pytest.mark.parametrize(
    "auth",
    [
        "unreviewed-auth-method",
        "synthetic-private-catalog-credential",
        {"token": "synthetic-private-catalog-credential"},
        {"value": "synthetic-private-catalog-credential"},
        ["own-auth"],
    ],
)
async def test_harness_http_redacts_unknown_auth_without_hiding_safe_siblings(
    stock_harness_http_inventory, auth
) -> None:
    from moonmind.omnigent.harness_platform.catalog_service import (
        InMemoryHarnessCatalogRepository,
        OmnigentHarnessCatalogService,
    )

    inventory = stock_harness_http_inventory
    inventory.responses["/v1/harnesses"]["data"].append(
        {
            "id": "unreviewed-plugin",
            "plugin_entry_point": "example.plugin:harness",
            "capabilities": {"auth": auth},
        }
    )
    catalogs = InMemoryHarnessCatalogRepository()
    synchronized = await OmnigentHarnessCatalogService(
        client=inventory.client,
        repository=catalogs,
        endpoint_ref="default",
        omnigent_build_digest="sha256:" + "6" * 64,
    ).synchronize()
    harnesses = {row.id: row for row in synchronized.snapshot.harnesses}
    assert harnesses["codex"].capabilities.authModel == "omnigent-credential"
    assert harnesses["unreviewed-plugin"].capabilities.authModel == "[REDACTED]"
    trust = {row.harnessId: row.trustState for row in synchronized.trust_records}
    assert trust["unreviewed-plugin"] == TrustState.quarantined
    assert "synthetic-private-catalog-credential" not in (
        synchronized.snapshot.model_dump_json() + json.dumps(synchronized.diagnostics)
    )
    assert await catalogs.latest("default") == synchronized
