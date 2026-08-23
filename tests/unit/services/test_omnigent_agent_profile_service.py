"""Stable identity and compatibility rules for Omnigent inventory sync."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from api_service.services.omnigent_agent_profile_service import (
    _bounded_metadata,
    projection_identity,
    projection_readiness,
)


def test_projection_identity_uses_stable_id_not_display_name():
    first = projection_identity("default", "agent-1", "v1")
    assert first == projection_identity("default", "agent-1", "v1")
    assert first != projection_identity("default", "agent-2", "v1")
    assert first != projection_identity("other", "agent-1", "v1")


def test_projection_identity_is_bounded_for_untrusted_upstream_values():
    result = projection_identity("e" * 1000, "a" * 10000, "v" * 1000)
    assert result.startswith("upstream:")
    assert len(result) == 73


def _projection(now: datetime, **overrides):
    values = {
        "available": True,
        "compatible": True,
        "last_successful_sync_at": now - timedelta(minutes=1),
        "last_attempt_at": now,
        "error": None,
        "bridge_mode": "proxy",
        "metadata_snapshot": {
            "harness": "codex-native",
            "capabilities": ["session.start"],
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_projection_readiness_requires_recent_success_without_error():
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)

    assert projection_readiness(_projection(now), now=now)["ready"] is True
    stale = projection_readiness(
        _projection(now, last_successful_sync_at=now - timedelta(minutes=6)),
        now=now,
    )
    failed = projection_readiness(_projection(now, error="endpoint timeout"), now=now)

    assert stale["freshness"] == "stale"
    assert stale["reason"] == "upstream inventory is stale"
    assert failed["freshness"] == "stale"
    assert failed["ready"] is False


def test_projection_readiness_explains_missing_unavailable_and_incompatible():
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)

    missing = projection_readiness(None, now=now)
    unavailable = projection_readiness(
        _projection(now, available=False),
        now=now,
    )
    incompatible = projection_readiness(
        _projection(now, compatible=False),
        now=now,
    )

    assert missing["freshness"] == "missing"
    assert unavailable["reason"] == "stable upstream identity is unavailable"
    assert incompatible["reason"] == "stable upstream identity is incompatible"


def test_projection_readiness_enforces_requested_contract():
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    projection = _projection(now)
    projection.bridge_mode = "proxy"
    projection.metadata_snapshot = {
        "harness": "codex-native",
        "capabilities": ["session.start"],
    }

    mismatch = projection_readiness(
        projection,
        now=now,
        bridge_mode="embedded",
        harness="other",
        required_capabilities=["tools"],
    )

    assert mismatch["ready"] is False
    assert mismatch["reason"] == "upstream metadata does not satisfy the requested profile contract"


def test_upstream_metadata_is_allowlisted_and_bounded():
    result = _bounded_metadata({
        "id": "agent-1",
        "name": "n" * 1000,
        "capabilities": ["tools", "session.start"],
        "apiToken": "must-not-persist",
        "nested": {"arbitrary": "payload"},
    })

    assert result["id"] == "agent-1"
    assert len(result["name"]) == 512
    assert result["capabilities"] == ["session.start", "tools"]
    assert "apiToken" not in result
    assert "nested" not in result


@pytest.mark.asyncio
async def test_synchronize_omnigent_harness_catalog_is_one_canonical_path(
    monkeypatch: pytest.MonkeyPatch,
):
    """Endpoint and startup reconciliation share one synchronization path."""

    from api_service.services import omnigent_agent_profile_service as service_module

    class _Snapshot:
        catalogRef = "cat-1"
        endpointRef = "default"
        observedAt = datetime.now(timezone.utc)
        omnigentVersion = "1.0.0"
        # Contains the native harness so the local OpenCode overlay is a no-op
        # in this canonical-path test.
        harnesses = [
            SimpleNamespace(id="codex"),
            SimpleNamespace(id="opencode-native"),
        ]
        pluginLoadErrors: list = []

    class _Result:
        snapshot = _Snapshot()
        trust_records = ()
        diagnostics = {"agents": [{"id": "opencode-native-ui", "version": "9"}]}

    class _CatalogService:
        async def synchronize(self):
            return _Result()

    built_with = {}

    def fake_build(*, session_factory):
        built_with["session_factory"] = session_factory
        return SimpleNamespace(catalog_service=_CatalogService())

    inventory_calls = []
    builtin_calls = []

    async def fake_inventory(session, *, endpoint_ref, bridge_mode, inventory):
        inventory_calls.append((endpoint_ref, bridge_mode, inventory))

    async def fake_builtin(*, session, catalog):
        builtin_calls.append(catalog)
        return {"profileId": "omnigent-opencode-default"}

    monkeypatch.setattr(
        "moonmind.omnigent.production.build_generic_omnigent_execution_services",
        fake_build,
    )
    monkeypatch.setattr(
        "api_service.api.routers.omnigent_agent_profiles.ensure_builtin_opencode_agent_profile",
        fake_builtin,
    )
    sentinel_factory = object()
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        sentinel_factory,
        raising=False,
    )
    monkeypatch.setattr(service_module, "synchronize_upstream_inventory", fake_inventory)

    summary = await service_module.synchronize_omnigent_harness_catalog(
        session=object()
    )

    assert built_with["session_factory"] is sentinel_factory
    assert summary == {
        "catalogRef": "cat-1",
        "observedAt": _Snapshot.observedAt,
        "omnigentVersion": "1.0.0",
        "harnessCount": 2,
        "pluginLoadErrors": [],
        "builtinAgentProfile": {"profileId": "omnigent-opencode-default"},
    }
    assert inventory_calls == [
        ("default", "proxy", [{"id": "opencode-native-ui", "version": "9"}])
    ]
    assert len(builtin_calls) == 1


@pytest.mark.asyncio
async def test_synchronize_omnigent_harness_catalog_propagates_endpoint_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    from api_service.services import omnigent_agent_profile_service as service_module

    class _FailingCatalogService:
        async def synchronize(self):
            raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr(
        "moonmind.omnigent.production.build_generic_omnigent_execution_services",
        lambda **kwargs: SimpleNamespace(
            catalog_service=_FailingCatalogService()
        ),
    )

    with pytest.raises(RuntimeError, match="endpoint unreachable"):
        await service_module.synchronize_omnigent_harness_catalog(session=object())


def test_overlay_adds_stable_opencode_identity_when_endpoint_lacks_it():
    from datetime import UTC, datetime

    from moonmind.omnigent.harness_platform.catalog import (
        create_catalog_snapshot,
        compute_catalog_ref,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        HarnessCatalogSyncResult,
    )

    from api_service.services.omnigent_agent_profile_service import (
        _overlay_synthetic_opencode,
        _synthetic_opencode_implementation,
    )

    observed = datetime(2026, 8, 23, tzinfo=UTC)
    snapshot = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="0.10.0",
        omnigentBuildDigest="sha256:" + "c" * 64,
        sourceDigest="sha256:" + "d" * 64,
        harnesses=[],
        observedAt=observed,
    )
    assert compute_catalog_ref(snapshot) == snapshot.catalogRef
    real = HarnessCatalogSyncResult(
        snapshot=snapshot,
        trust_records=(),
        diagnostics={"agents": [], "agentCount": 0},
    )

    merged = _overlay_synthetic_opencode(real)

    assert merged is not real
    assert [h.id for h in merged.snapshot.harnesses] == ["opencode-native"]
    implementation_ref = (
        _synthetic_opencode_implementation().implementation_ref()
    )
    assert any(
        record.implementationRef == implementation_ref
        and record.trustState.value == "core_trusted"
        for record in merged.trust_records
    )
    assert merged.snapshot.observedAt > real.snapshot.observedAt
    agents = merged.diagnostics["agents"]
    assert {
        "id": "opencode-native-ui",
        "version": "1",
        "harness": "opencode-native",
    } in agents

    # Deterministic content: a later observation of unchanged inventory
    # produces the same source digest so profile versions stay stable.
    again = _overlay_synthetic_opencode(real)
    assert again.snapshot.sourceDigest == merged.snapshot.sourceDigest
    assert again.snapshot.observedAt == merged.snapshot.observedAt


def test_overlay_skips_when_harness_present_or_support_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    from datetime import UTC, datetime

    from moonmind.omnigent.harness_platform.catalog import (
        create_catalog_snapshot,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        HarnessCatalogSyncResult,
    )

    from api_service.services.omnigent_agent_profile_service import (
        _overlay_synthetic_opencode,
    )

    def _result_with(harness_rows):
        return HarnessCatalogSyncResult(
            snapshot=create_catalog_snapshot(
                endpointRef="default",
                omnigentVersion="0.10.0",
                omnigentBuildDigest="sha256:" + "c" * 64,
                sourceDigest="sha256:" + "d" * 64,
                harnesses=harness_rows,
                observedAt=datetime(2026, 8, 23, tzinfo=UTC),
            ),
            trust_records=(),
            diagnostics={"agents": []},
        )

    native_row = {
        "id": "opencode-native",
        "label": "OpenCode",
        "implementation": {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "e" * 64,
            "pluginEntryPoint": None,
        },
        "capabilities": {"integrationMode": "native-server"},
    }
    assert (
        _overlay_synthetic_opencode(_result_with([native_row])) is not None
    )
    result = _result_with([native_row])
    assert _overlay_synthetic_opencode(result) is result

    monkeypatch.setenv("MOONMIND_OMNIGENT_OPENCODE_ENABLED", "false")
    empty = _result_with([])
    assert _overlay_synthetic_opencode(empty) is empty
