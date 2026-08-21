"""Authenticated Omnigent inventory synchronization and durable projection.

The service owns the only production path that turns ``/v1/harnesses``,
``/v1/agents`` and ``/v1/hosts`` into planner authority.  Implementation
identity is bound to the exact, digest-pinned Omnigent build and the normalized
catalog row; harness names alone never carry trust.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from moonmind.omnigent.harness_platform.catalog import (
    HarnessCapabilities,
    HarnessCatalogSnapshot,
    HarnessImplementationIdentity,
    HarnessRecord,
    HarnessTrustRecord,
    TrustState,
    classify_harness_trust,
    create_catalog_snapshot,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


class OmnigentInventoryClient(Protocol):
    async def get_version(self) -> str: ...

    async def list_harnesses(self) -> list[dict[str, Any]]: ...

    async def list_agents(self) -> list[dict[str, Any]]: ...

    async def list_hosts(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class HarnessCatalogSyncResult:
    snapshot: HarnessCatalogSnapshot
    trust_records: tuple[HarnessTrustRecord, ...]
    diagnostics: Mapping[str, Any]


class OmnigentHarnessCatalogRepository(Protocol):
    async def persist(
        self,
        result: HarnessCatalogSyncResult,
    ) -> HarnessCatalogSyncResult: ...

    async def load(self, catalog_ref: str) -> HarnessCatalogSyncResult | None: ...

    async def latest(self, endpoint_ref: str) -> HarnessCatalogSyncResult | None: ...


def _canonical_digest(value: Any) -> str:
    body = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _bounded_rows(
    rows: Sequence[Mapping[str, Any]], *, label: str
) -> list[dict[str, Any]]:
    if len(rows) > 10_000:
        raise HarnessPlatformError(
            f"Omnigent {label} inventory exceeds the 10000-row synchronization bound",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE,
        )
    normalized: list[dict[str, Any]] = []
    for row in rows:
        encoded = json.dumps(row, sort_keys=True, default=str)
        if len(encoded.encode("utf-8")) > 256_000:
            raise HarnessPlatformError(
                f"Omnigent {label} row exceeds the synchronization size bound",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE,
            )
        normalized.append(json.loads(encoded))
    return sorted(
        normalized, key=lambda item: str(item.get("id") or item.get("host_id") or "")
    )


def _normalize_capabilities(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    # Omnigent's wire contract is snake_case while MoonMind's durable catalog
    # schema uses stable camelCase field aliases. Normalize explicitly at the
    # endpoint boundary; silently dropping integration/auth fields would make a
    # real catalog look incompatible even though synthetic test rows worked.
    wire_to_schema = {
        "integration_mode": "integrationMode",
        "auth": "authModel",
        "auth_model": "authModel",
        "fork_history": "forkHistory",
        "model_family": "modelFamily",
        "effort": "effortFamily",
        "effort_family": "effortFamily",
        "live_queue": "liveQueue",
    }
    allowed = {
        field.alias or name for name, field in HarnessCapabilities.model_fields.items()
    }
    normalized: dict[str, Any] = {}
    for raw_key, item in value.items():
        key = wire_to_schema.get(str(raw_key), str(raw_key))
        if key in allowed:
            normalized[key] = item
    return normalized


def _normalize_harness(
    row: Mapping[str, Any],
    *,
    omnigent_version: str,
    omnigent_build_digest: str,
) -> HarnessRecord:
    harness_id = str(row.get("id") or "").strip()
    if not harness_id:
        raise HarnessPlatformError(
            "Omnigent returned a harness without an id",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE,
        )
    plugin_entry_point = (
        str(row.get("plugin_entry_point") or row.get("pluginEntryPoint") or "").strip()
        or None
    )
    source_kind = "plugin" if plugin_entry_point else "core"
    package = str(row.get("package") or "omnigent").strip().lower()
    implementation_material = {
        "omnigentBuildDigest": omnigent_build_digest,
        "harness": dict(row),
    }
    implementation = HarnessImplementationIdentity(
        sourceKind=source_kind,
        package=package,
        version=omnigent_version,
        digest=_canonical_digest(implementation_material),
        pluginEntryPoint=plugin_entry_point,
    )
    aliases = row.get("aliases") if isinstance(row.get("aliases"), list) else []
    setup_steps = row.get("setup_steps", row.get("setupSteps", []))
    if not isinstance(setup_steps, list):
        setup_steps = []
    runtime_requirements = row.get(
        "runtime_requirements", row.get("runtimeRequirements", {})
    )
    if not isinstance(runtime_requirements, Mapping):
        runtime_requirements = {}
    return HarnessRecord.model_validate(
        {
            "id": harness_id,
            "aliases": [str(item) for item in aliases],
            "label": str(row.get("label") or harness_id),
            "implementation": implementation.model_dump(by_alias=True, mode="json"),
            "runtimeRequirements": dict(runtime_requirements),
            "capabilities": _normalize_capabilities(row.get("capabilities")),
            "setupSteps": [item for item in setup_steps if isinstance(item, Mapping)],
        }
    )


class OmnigentHarnessCatalogService:
    """Synchronize an authenticated endpoint into immutable planner authority."""

    def __init__(
        self,
        *,
        client: OmnigentInventoryClient,
        repository: OmnigentHarnessCatalogRepository,
        endpoint_ref: str,
        omnigent_build_digest: str,
        trust_policy: Callable[[HarnessRecord], TrustState] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not omnigent_build_digest.startswith("sha256:")
            or len(omnigent_build_digest) != 71
        ):
            raise ValueError("omnigent_build_digest must be an immutable sha256 digest")
        self._client = client
        self._repository = repository
        self._endpoint_ref = endpoint_ref
        self._build_digest = omnigent_build_digest
        self._trust_policy = trust_policy or self._default_trust_policy
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _default_trust_policy(record: HarnessRecord) -> TrustState:
        # Core code is part of the authenticated, digest-pinned Omnigent build.
        # Plugins require an explicit approval record after discovery.
        if record.implementation.sourceKind == "core":
            return TrustState.core_trusted
        return TrustState.quarantined

    async def synchronize(self) -> HarnessCatalogSyncResult:
        version = str(await self._client.get_version()).strip()
        harness_rows = _bounded_rows(
            await self._client.list_harnesses(), label="harness"
        )
        agent_rows = _bounded_rows(await self._client.list_agents(), label="agent")
        host_rows = _bounded_rows(await self._client.list_hosts(), label="host")
        observed_at = self._clock().astimezone(UTC)
        source = {
            "version": version,
            "buildDigest": self._build_digest,
            "harnesses": harness_rows,
            "agents": agent_rows,
            "hosts": host_rows,
        }
        source_digest = _canonical_digest(source)
        harnesses = [
            _normalize_harness(
                row,
                omnigent_version=version,
                omnigent_build_digest=self._build_digest,
            )
            for row in harness_rows
        ]
        plugin_errors = [
            {
                "harnessId": record.id,
                "error": str(
                    row.get("plugin_load_error") or row.get("pluginLoadError")
                ),
            }
            for row, record in zip(harness_rows, harnesses, strict=True)
            if row.get("plugin_load_error") or row.get("pluginLoadError")
        ]
        snapshot = create_catalog_snapshot(
            endpointRef=self._endpoint_ref,
            omnigentVersion=version,
            omnigentBuildDigest=self._build_digest,
            observedAt=observed_at,
            sourceDigest=source_digest,
            harnesses=[
                item.model_dump(by_alias=True, mode="json") for item in harnesses
            ],
            pluginLoadErrors=plugin_errors,
        )
        trust_records = tuple(
            classify_harness_trust(
                harnessId=record.id,
                implementation=record.implementation,
                trustState=(
                    TrustState.quarantined
                    if any(error["harnessId"] == record.id for error in plugin_errors)
                    else self._trust_policy(record)
                ),
                decidedBy="catalog-sync",
                decidedAt=observed_at,
            )
            for record in harnesses
        )
        result = HarnessCatalogSyncResult(
            snapshot=snapshot,
            trust_records=trust_records,
            diagnostics={
                "agentCount": len(agent_rows),
                "hostCount": len(host_rows),
                "harnessCount": len(harnesses),
                "lastSyncAt": observed_at.isoformat(),
                "pluginLoadErrors": plugin_errors,
                # These are bounded inventory projections, not a second planner authority.
                "agents": agent_rows,
                "hosts": host_rows,
            },
        )
        return await self._repository.persist(result)


class InMemoryHarnessCatalogRepository:
    def __init__(self) -> None:
        self._results: dict[str, HarnessCatalogSyncResult] = {}

    async def persist(
        self, result: HarnessCatalogSyncResult
    ) -> HarnessCatalogSyncResult:
        existing = self._results.get(result.snapshot.catalogRef)
        if existing is not None and existing != result:
            raise HarnessPlatformError(
                f"catalog conflict for {result.snapshot.catalogRef}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        self._results[result.snapshot.catalogRef] = result
        return result

    async def load(self, catalog_ref: str) -> HarnessCatalogSyncResult | None:
        return self._results.get(catalog_ref)

    async def latest(self, endpoint_ref: str) -> HarnessCatalogSyncResult | None:
        matching = [
            item
            for item in self._results.values()
            if item.snapshot.endpointRef == endpoint_ref
        ]
        return max(matching, key=lambda item: item.snapshot.observedAt, default=None)


class DbHarnessCatalogRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def persist(
        self, result: HarnessCatalogSyncResult
    ) -> HarnessCatalogSyncResult:
        from api_service.db.models import OmnigentHarnessCatalogSnapshotRecord
        from api_service.db.models import OmnigentHarnessTrustRecord as DbTrustRecord

        snapshot = result.snapshot
        async with self._session_factory() as session:
            existing = await session.get(
                OmnigentHarnessCatalogSnapshotRecord, snapshot.catalogRef
            )
            if existing is None:
                session.add(
                    OmnigentHarnessCatalogSnapshotRecord(
                        catalog_ref=snapshot.catalogRef,
                        endpoint_ref=snapshot.endpointRef,
                        omnigent_version=snapshot.omnigentVersion,
                        omnigent_build_digest=snapshot.omnigentBuildDigest,
                        observed_at=snapshot.observedAt,
                        source_digest=snapshot.sourceDigest,
                        snapshot_json=snapshot.model_dump(by_alias=True, mode="json"),
                        diagnostics_json=dict(result.diagnostics),
                    )
                )
            for trust in result.trust_records:
                row = await session.get(DbTrustRecord, trust.implementationRef)
                values = {
                    "harness_id": trust.harnessId,
                    "catalog_ref": snapshot.catalogRef,
                    "trust_state": trust.trustState.value,
                    "reason_code": (
                        "plugin_load_error"
                        if any(
                            item.get("harnessId") == trust.harnessId
                            for item in snapshot.pluginLoadErrors
                        )
                        else None
                    ),
                    "plugin_load_error_json": next(
                        (
                            dict(item)
                            for item in snapshot.pluginLoadErrors
                            if item.get("harnessId") == trust.harnessId
                        ),
                        None,
                    ),
                }
                if row is None:
                    session.add(
                        DbTrustRecord(
                            implementation_ref=trust.implementationRef, **values
                        )
                    )
                else:
                    # The identity key is exact; sync may refresh diagnostics but cannot
                    # silently upgrade an operator-blocked or approved plugin decision.
                    if row.trust_state in {
                        TrustState.plugin_approved.value,
                        TrustState.blocked.value,
                    }:
                        continue
                    # Trust is authority for the exact implementation, not for
                    # whichever catalog happened to observe it most recently.
                    # Keep its original catalog association so refreshing the
                    # inventory cannot make an older immutable snapshot lose
                    # its trust evidence.
                    values.pop("catalog_ref", None)
                    for key, value in values.items():
                        setattr(row, key, value)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                loaded = await self.load(snapshot.catalogRef)
                if loaded is not None:
                    return loaded
                raise HarnessPlatformError(
                    "catalog persistence conflict",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                ) from exc
        return result

    async def load(self, catalog_ref: str) -> HarnessCatalogSyncResult | None:
        from api_service.db.models import OmnigentHarnessCatalogSnapshotRecord
        from api_service.db.models import OmnigentHarnessTrustRecord as DbTrustRecord

        async with self._session_factory() as session:
            row = await session.get(OmnigentHarnessCatalogSnapshotRecord, catalog_ref)
            if row is None:
                return None
            snapshot = HarnessCatalogSnapshot.model_validate(row.snapshot_json)
            implementations = {
                item.implementation.implementation_ref(): item.implementation
                for item in snapshot.harnesses
            }
            records = (
                (
                    await session.execute(
                        select(DbTrustRecord).where(
                            DbTrustRecord.implementation_ref.in_(tuple(implementations))
                        )
                    )
                )
                .scalars()
                .all()
            )
            trust = tuple(
                classify_harness_trust(
                    harnessId=item.harness_id,
                    implementation=implementations[item.implementation_ref],
                    trustState=TrustState(item.trust_state),
                    decidedBy="catalog-repository",
                    decidedAt=item.updated_at or item.created_at,
                )
                for item in records
            )
            return HarnessCatalogSyncResult(
                snapshot=snapshot,
                trust_records=trust,
                diagnostics=dict(row.diagnostics_json or {}),
            )

    async def latest(self, endpoint_ref: str) -> HarnessCatalogSyncResult | None:
        from api_service.db.models import OmnigentHarnessCatalogSnapshotRecord

        async with self._session_factory() as session:
            ref = (
                await session.execute(
                    select(OmnigentHarnessCatalogSnapshotRecord.catalog_ref)
                    .where(
                        OmnigentHarnessCatalogSnapshotRecord.endpoint_ref
                        == endpoint_ref
                    )
                    .order_by(OmnigentHarnessCatalogSnapshotRecord.observed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        return await self.load(ref) if ref else None


__all__ = [
    "DbHarnessCatalogRepository",
    "HarnessCatalogSyncResult",
    "InMemoryHarnessCatalogRepository",
    "OmnigentHarnessCatalogRepository",
    "OmnigentHarnessCatalogService",
]
