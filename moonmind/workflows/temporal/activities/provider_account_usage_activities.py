"""Activities that collect provider account usage under credential authority.

``MoonMind.ProviderAccountUsageCollection`` runs these for one refresh
attempt the API's ``ProviderAccountUsageService`` claimed. Each step re-reads
trusted state; results reach the cache only through that service, which
fences them on credential generation, attempt ownership and the bound account.
Activities return bounded status fields only: tokens, credential contents and
raw provider output never enter Temporal history, logs or artifacts. Failures
change observation state only, never readiness, cooldowns, capacity, defaults,
enablement or routing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Mapping

from temporalio import activity

from moonmind.provider_profiles.account_usage import (
    AccountUsageFailureCode,
    parse_stored_instant,
)
from moonmind.provider_profiles.usage_collection import (
    CredentialOwner,
    IdleProbeContext,
    OwnerUsageRequest,
    ProbeResult,
    UsageCollectionRoute,
    UsageCollectionStatus,
    UsageCollectionTarget,
    UsageCollectorRegistry,
    UsageProbePlan,
    bind_owner_report,
    credential_resource_key,
    default_usage_collectors,
    resolve_credential_consumers,
)

logger = logging.getLogger(__name__)

_HEARTBEAT_SECONDS = 5.0
_MAX_OWNERS = 8


@dataclass
class UsageCollectionEnvironment:
    """Production collaborators; tests substitute fakes through ``use_environment``."""

    session_factory: Callable[[], Any]
    collectors: Callable[[], UsageCollectorRegistry] = default_usage_collectors
    lease_client: Callable[[], Any] | None = None
    probe_runner: Callable[[], Any] | None = None
    resolve_launch: Callable[[str], Awaitable[Mapping[str, Any]]] | None = None
    runtime_bindings: Callable[[], Any] | None = None
    host_leases: Callable[[], Any] | None = None
    clock: Callable[[], datetime] | None = None


_environment: UsageCollectionEnvironment | None = None


def use_environment(environment: UsageCollectionEnvironment | None) -> None:
    global _environment
    _environment = environment


def _env() -> UsageCollectionEnvironment:
    if _environment is not None:
        return _environment
    from api_service.db.base import async_session_maker

    return UsageCollectionEnvironment(session_factory=async_session_maker)


def _lease_client(env: UsageCollectionEnvironment) -> Any:
    if env.lease_client is not None:
        return env.lease_client()
    from moonmind.provider_profiles.lease_client import ProviderProfileLeaseClient
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    return ProviderProfileLeaseClient(TemporalClientAdapter())


def _probe_runner(env: UsageCollectionEnvironment) -> Any:
    if env.probe_runner is not None:
        return env.probe_runner()
    from moonmind.omnigent.host_services.usage_probe import UsageProbeRunner

    return UsageProbeRunner()


async def _resolve_launch(env: UsageCollectionEnvironment, profile_id: str) -> Any:
    if env.resolve_launch is not None:
        return await env.resolve_launch(profile_id)
    from moonmind.workflows.temporal.activities.oauth_session_activities import (
        resolve_on_demand_launch,
    )

    _profile_ref, _policy_ref, launch = await resolve_on_demand_launch(profile_id)
    return launch


def _target(request: Mapping[str, Any]) -> UsageCollectionTarget:
    deadline = parse_stored_instant(request.get("deadline_at"))
    if deadline is None:
        raise ValueError("deadline_at is required")
    return UsageCollectionTarget(
        profile_id=str(request["profile_id"]),
        credential_generation=int(request["credential_generation"]),
        provider_id=str(request["provider_id"]),
        runtime_id=str(request["runtime_id"]),
        attempt_id=str(request["attempt_id"]),
        deadline_at=deadline,
    )


def _heartbeat(details: Mapping[str, Any]) -> None:
    try:
        activity.heartbeat(dict(details))
    except RuntimeError:
        # Called outside a Temporal activity (direct unit invocation).
        pass


async def _with_service(env: UsageCollectionEnvironment, call: Callable) -> Any:
    from api_service.services.provider_account_usage import ProviderAccountUsageService

    async with env.session_factory() as session:
        kwargs = {"clock": env.clock} if env.clock is not None else {}
        return await call(ProviderAccountUsageService(session, **kwargs))


async def _defer(
    env: UsageCollectionEnvironment, target: UsageCollectionTarget
) -> dict:
    result = await _with_service(
        env,
        lambda service: service.defer_collection(
            profile_id=target.profile_id,
            credential_generation=target.credential_generation,
            attempt_id=target.attempt_id,
        ),
    )
    status = (
        UsageCollectionStatus.DEFERRED if result else UsageCollectionStatus.OBSOLETE
    )
    return {"status": status.value}


async def _fail(
    env: UsageCollectionEnvironment,
    target: UsageCollectionTarget,
    code: AccountUsageFailureCode,
    retry_after: Any = None,
) -> dict:
    result = await _with_service(
        env,
        lambda service: service.record_collection_failure(
            profile_id=target.profile_id,
            credential_generation=target.credential_generation,
            attempt_id=target.attempt_id,
            code=code,
            retry_after=retry_after,
        ),
    )
    if not result.accepted:
        return {"status": UsageCollectionStatus.OBSOLETE.value}
    return {"status": UsageCollectionStatus.FAILED.value, "failure": code.value}


async def _publish(
    env: UsageCollectionEnvironment, target: UsageCollectionTarget, report: Any
) -> dict:
    observation = bind_owner_report(target, report)
    result = await _with_service(
        env, lambda service: service.ingest_observation(observation)
    )
    if not result.accepted:
        return {
            "status": UsageCollectionStatus.OBSOLETE.value,
            "rejection": result.rejection.value if result.rejection else None,
        }
    return {
        "status": UsageCollectionStatus.OBSERVED.value,
        "snapshotRevision": result.snapshot_revision,
    }


async def _active_ledger_leases(session: Any, profile_ids: list[str]) -> list[dict]:
    from sqlalchemy import select

    from api_service.db.models import ProviderProfileSlotLease
    from moonmind.provider_profiles.lease_client import ACTIVE_DURABLE_LEASE_STATES

    rows = await session.execute(
        select(
            ProviderProfileSlotLease.profile_id,
            ProviderProfileSlotLease.lease_id,
            ProviderProfileSlotLease.workflow_id,
        ).where(
            ProviderProfileSlotLease.profile_id.in_(profile_ids),
            ProviderProfileSlotLease.lease_state.in_(
                sorted(ACTIVE_DURABLE_LEASE_STATES)
            ),
        )
    )
    return [
        {
            "profile_id": row.profile_id,
            "lease_id": row.lease_id,
            "workflow_id": row.workflow_id,
        }
        for row in rows.all()
    ]


async def resolve_collection(request: Mapping[str, Any]) -> dict[str, Any]:
    """Claim the attempt, then route it to the active owner or an idle probe."""

    from sqlalchemy import select

    from api_service.db.models import ManagedAgentProviderProfile

    env = _env()
    target = _target(request)
    claimed = await _with_service(
        env,
        lambda service: service.mark_running(
            profile_id=target.profile_id,
            credential_generation=target.credential_generation,
            attempt_id=target.attempt_id,
        ),
    )
    if not claimed:
        return {"status": UsageCollectionStatus.OBSOLETE.value}
    adapter = env.collectors().get(target.runtime_id, target.provider_id)
    async with env.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, target.profile_id)
        volume_ref = str(getattr(profile, "volume_ref", "") or "").strip()
        bound = (
            profile is not None
            and int(profile.credential_generation) == target.credential_generation
            and profile.runtime_id == target.runtime_id
            and profile.provider_id == target.provider_id
        )
        aliases: list[str] = []
        ledger: list[dict] = []
        if bound and volume_ref:
            # Every profile bound to the same enrolled home shares its writer.
            aliases = sorted(
                (
                    await session.execute(
                        select(ManagedAgentProviderProfile.profile_id).where(
                            ManagedAgentProviderProfile.volume_ref == volume_ref
                        )
                    )
                ).scalars()
            )
            ledger = await _active_ledger_leases(session, aliases)
    if not bound:
        return {"status": UsageCollectionStatus.OBSOLETE.value}
    if not volume_ref:
        # Only an enrolled OAuth home carries account usage authority.
        return await _fail(env, target, AccountUsageFailureCode.USAGE_ACCESS_MISSING)
    if adapter is None:
        # The coordinating process saw a collector this worker does not have.
        return await _fail(env, target, AccountUsageFailureCode.DISPATCH_FAILED)
    owners = await resolve_credential_consumers(
        aliases,
        runtime_bindings=_runtime_bindings(env),
        host_leases=_host_leases(env),
        active_ledger_leases=ledger,
    )
    return {
        "status": "ready",
        "route": (
            UsageCollectionRoute.ACTIVE_OWNER
            if owners
            else UsageCollectionRoute.IDLE_PROBE
        ).value,
        "credentialResourceKey": credential_resource_key(
            runtime_id=target.runtime_id, volume_ref=volume_ref
        ),
        "aliasProfileIds": aliases,
        "owners": [asdict(owner) for owner in owners[:_MAX_OWNERS]],
    }


def _runtime_bindings(env: UsageCollectionEnvironment) -> Any:
    if env.runtime_bindings is not None:
        return env.runtime_bindings()
    from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore

    return DbRuntimeBindingStore(env.session_factory)


def _host_leases(env: UsageCollectionEnvironment) -> Any:
    if env.host_leases is not None:
        return env.host_leases()
    from moonmind.omnigent.host_leases import DbOmnigentHostLeaseRepository

    return DbOmnigentHostLeaseRepository(env.session_factory)


async def collect_from_owner(request: Mapping[str, Any]) -> dict[str, Any]:
    """Ask the active owner through the adapter; defer when it cannot answer.

    The owner is re-resolved from trusted state before the report is
    published, so a result from an owner that has since stopped is dropped.
    """

    env = _env()
    target = _target(request)
    adapter = env.collectors().get(target.runtime_id, target.provider_id)
    owners = [
        CredentialOwner(**owner)
        for owner in request.get("owners") or ()
        if owner.get("kind") == "runtime_binding"
    ]
    if adapter is None:
        return await _fail(env, target, AccountUsageFailureCode.DISPATCH_FAILED)
    for owner in owners:
        remaining = (target.deadline_at - _now(env)).total_seconds()
        if remaining <= 0:
            break
        try:
            report = await asyncio.wait_for(
                adapter.request_from_owner(
                    OwnerUsageRequest(target=target, owner=owner)
                ),
                timeout=remaining,
            )
        except TimeoutError:
            return await _fail(env, target, AccountUsageFailureCode.TIMEOUT)
        except Exception as exc:
            logger.warning(
                "Account usage owner request failed for profile %s: %s",
                target.profile_id,
                type(exc).__name__,
            )
            return await _fail(env, target, AccountUsageFailureCode.INTERNAL_ERROR)
        if report is None:
            continue
        current = await resolve_credential_consumers(
            [owner.profile_id],
            runtime_bindings=_runtime_bindings(env),
            host_leases=_host_leases(env),
            active_ledger_leases=(),
        )
        if not any(item.owner_ref == owner.owner_ref for item in current):
            return {"status": UsageCollectionStatus.OBSOLETE.value}
        return await _publish(env, target, report)
    # The active mode cannot provide an observation; keep cached data.
    return await _defer(env, target)


def _now(env: UsageCollectionEnvironment) -> datetime:
    return env.clock() if env.clock is not None else datetime.now(UTC)


async def try_acquire_grant(request: Mapping[str, Any]) -> dict[str, Any]:
    """Take the non-blocking idle grant; defer the attempt when busy."""

    env = _env()
    target = _target(request)
    owner_id = str(request["owner_id"])
    lease = await _lease_client(env).try_acquire_usage_observation_lease(
        runtime_id=target.runtime_id,
        profile_id=target.profile_id,
        owner_id=owner_id,
        metadata={
            "workflowId": owner_id,
            "credentialGeneration": target.credential_generation,
            "credentialResourceKey": str(request["credentialResourceKey"]),
            "credentialAliasProfileIds": list(request.get("aliasProfileIds") or ()),
        },
    )
    if lease is None:
        return {"granted": False, **await _defer(env, target)}
    return {
        "granted": True,
        "leaseId": lease.lease_id,
        "fencingGeneration": lease.fencing_generation,
    }


async def _grant_is_current(
    env: UsageCollectionEnvironment, request: Mapping[str, Any]
) -> bool:
    """Whether the observation lease is still held under the same grant."""

    from moonmind.provider_profiles.lease_client import (
        CredentialLease,
        CredentialLeasePurpose,
    )

    lease_id = str(request["leaseId"])
    state = await _lease_client(env).inspect_lease(
        CredentialLease(
            profile_id=str(request["profile_id"]),
            runtime_id=str(request["runtime_id"]),
            lease_id=lease_id,
            owner_id=lease_id,
            purpose=CredentialLeasePurpose.USAGE_OBSERVATION,
        )
    )
    fence = request.get("fencingGeneration")
    return bool(
        state.get("active")
        and state.get("profile_id") == request["profile_id"]
        and state.get("purpose") == CredentialLeasePurpose.USAGE_OBSERVATION.value
        and (fence is None or int(state.get("fencingGeneration") or 0) == int(fence))
    )


async def run_idle_probe(request: Mapping[str, Any]) -> dict[str, Any]:
    """Run the adapter's probe under the exclusive grant and publish the result.

    Grant and generation are validated before credential access and again
    before persistence. Cancellation (preemption) removes the container and
    proves it is gone before the cancellation completes.
    """

    from moonmind.omnigent.credential_materializers import load_profile_credential_home

    env = _env()
    target = _target(request)
    lease_id = str(request["leaseId"])
    adapter = env.collectors().get(target.runtime_id, target.provider_id)
    runner = _probe_runner(env)
    if adapter is None:
        return await _fail(env, target, AccountUsageFailureCode.DISPATCH_FAILED)
    if not await _grant_is_current(env, request):
        return {"status": UsageCollectionStatus.OBSOLETE.value}
    try:
        home = await load_profile_credential_home(
            env.session_factory,
            profile_id=target.profile_id,
            credential_generation=target.credential_generation,
        )
    except Exception:
        return {"status": UsageCollectionStatus.OBSOLETE.value}
    context = IdleProbeContext(target=target, credential_home_path=home.target_path)
    plan = adapter.idle_probe(context)
    if plan is None:
        return await _defer(env, target)
    try:
        launch = await _resolve_launch(env, target.profile_id)
    except Exception as exc:
        logger.warning(
            "Account usage probe launch unavailable for profile %s: %s",
            target.profile_id,
            type(exc).__name__,
        )
        return await _fail(env, target, AccountUsageFailureCode.INTERNAL_ERROR)

    async def run(plan: UsageProbePlan) -> ProbeResult:
        task = asyncio.ensure_future(
            runner.run(
                lease_id=lease_id,
                profile_id=target.profile_id,
                credential_generation=target.credential_generation,
                launch=launch,
                home=home,
                plan=plan,
                exclusive_grant=True,
            )
        )
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=_HEARTBEAT_SECONDS)
                if done:
                    break
                _heartbeat({"phase": "probe", "profileId": target.profile_id})
            code, stdout = task.result()
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # Ownership is handed back only after the container is proven gone.
            await asyncio.shield(runner.cleanup(lease_id))
            raise
        return adapter.parse_probe(plan, code, stdout)

    try:
        parsed = await run(plan)
        if parsed.recovery_required:
            # The stored access token expired. Only the adapter's native
            # recovery action may renew it, and only under this grant.
            recovery = adapter.recovery_probe(context)
            if recovery is None:
                return await _fail(
                    env, target, AccountUsageFailureCode.CREDENTIAL_REFRESH_UNAVAILABLE
                )
            parsed = await run(recovery)
            if parsed.recovery_required:
                return await _fail(
                    env, target, AccountUsageFailureCode.CREDENTIAL_REFRESH_UNAVAILABLE
                )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Account usage probe failed for profile %s: %s",
            target.profile_id,
            type(exc).__name__,
        )
        return await _fail(env, target, AccountUsageFailureCode.INTERNAL_ERROR)
    if not await _grant_is_current(env, request):
        return {"status": UsageCollectionStatus.OBSOLETE.value}
    if parsed.report is None:
        return await _fail(env, target, parsed.failure, parsed.retry_after)
    return await _publish(env, target, parsed.report)


async def cleanup_probe(request: Mapping[str, Any]) -> dict[str, Any]:
    """Prove the grant's probe container is gone; defer a preempted attempt."""

    env = _env()
    evidence = await _probe_runner(env).cleanup(str(request["leaseId"]))
    if request.get("deferAttempt"):
        await _defer(env, _target(request))
    return {"verified": True, **evidence}


@activity.defn(name="provider_account_usage.resolve")
async def provider_account_usage_resolve(request: Mapping[str, Any]) -> dict[str, Any]:
    return await resolve_collection(request)


@activity.defn(name="provider_account_usage.collect_from_owner")
async def provider_account_usage_collect_from_owner(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    return await collect_from_owner(request)


@activity.defn(name="provider_account_usage.try_acquire")
async def provider_account_usage_try_acquire(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    return await try_acquire_grant(request)


@activity.defn(name="provider_account_usage.probe")
async def provider_account_usage_probe(request: Mapping[str, Any]) -> dict[str, Any]:
    return await run_idle_probe(request)


@activity.defn(name="provider_account_usage.cleanup")
async def provider_account_usage_cleanup(request: Mapping[str, Any]) -> dict[str, Any]:
    return await cleanup_probe(request)


__all__ = [
    "UsageCollectionEnvironment",
    "cleanup_probe",
    "collect_from_owner",
    "provider_account_usage_cleanup",
    "provider_account_usage_collect_from_owner",
    "provider_account_usage_probe",
    "provider_account_usage_resolve",
    "provider_account_usage_try_acquire",
    "resolve_collection",
    "run_idle_probe",
    "try_acquire_grant",
    "use_environment",
]
