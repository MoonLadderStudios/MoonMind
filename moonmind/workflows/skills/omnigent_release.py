"""Singular Omnigent release alignment for the deployment update flow.

A deployment runs exactly one Omnigent release: one digest-pinned server
image plus digest-pinned host images, recorded once in the deployment
desired-state files. The release controller is the single writer; every
other authority (Compose rendering, launch policy versions, schedule
admissions, dispatch) derives from that record instead of independently
resolving mutable tags on its own cadence.

This ends the stale-pin saga where each authority pinned a different
upstream moment: the schedule input says 0.13, the policy says 0.13, the
running container says 0.14, and every dispatch fails until a human
re-admits each layer by hand. With one record, ``update-moonmind.sh``
advances the whole deployment in one audited operation; the major.minor
dispatch gate stays in place as a backstop that can then only fire on
genuine out-of-band drift.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Release-owned desired-state env keys. These live in the release-owned
# `.env.deploy` file (never the deployment-owned `.env`), so Compose renders
# the recorded digests and the tag inputs in `.env` stay untouched.
OMNIGENT_RELEASE_ENV_KEYS = (
    "OMNIGENT_IMAGE_REF",
    "OMNIGENT_HOST_IMAGE_REF",
    "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
    "OMNIGENT_SHARED_HOST_IMAGE_REF",
    "OMNIGENT_PI_HOST_IMAGE_REF",
)

# Record key inside the desired-state JSON sidecar.
OMNIGENT_RELEASE_RECORD_KEY = "omnigentRelease"

# Host kinds in the release record, keyed to match the bootstrap policy
# definitions (`host_image_kind`).
OMNIGENT_RELEASE_HOST_KINDS = ("codex", "opencode", "shared", "pi")

# Deployment-owned tag inputs the release resolves into digests. REF keys are
# deliberately absent: inputs are always tags, authority is always digests.
OMNIGENT_RELEASE_INPUT_KEYS = (
    "OMNIGENT_IMAGE",
    "OMNIGENT_IMAGE_TAG",
    "OMNIGENT_HOST_IMAGE",
    "OMNIGENT_HOST_IMAGE_TAG",
    "OMNIGENT_OPENCODE_HOST_IMAGE",
    "OMNIGENT_OPENCODE_HOST_IMAGE_TAG",
    "OMNIGENT_SHARED_HOST_IMAGE",
    "OMNIGENT_SHARED_HOST_IMAGE_TAG",
    "OMNIGENT_PI_HOST_IMAGE",
    "OMNIGENT_PI_HOST_IMAGE_TAG",
)

# Bootstrap policy ids the release migrates, with the record host kind each
# one pins. Mirrors `_bootstrap_policy_definitions` without importing the
# policy module at load time.
OMNIGENT_RELEASE_POLICIES = (
    ("omnigent-codex", "codex"),
    ("codex-static", "codex"),
    ("codex-on-demand", "codex"),
    ("omnigent-on-demand", "opencode"),
    ("opencode-on-demand", "opencode"),
)


class OmnigentReleaseError(ValueError):
    """A bounded omnigent migration step failed; retained fleet owns recovery."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"omnigent release migration failed at {step}: {message}")
        self.step = step


@dataclass(frozen=True, slots=True)
class OmnigentRelease:
    """One deployment-wide Omnigent release identity."""

    revision: int
    server_image_ref: str
    host_image_refs: dict[str, str]
    updated_at: str
    updated_by: str
    previous: dict[str, Any] | None = None

    def refs(self) -> dict[str, str]:
        """Return every digest-pinned ref in this release, keyed by kind."""
        return {
            "server": self.server_image_ref,
            **{k: v for k, v in self.host_image_refs.items() if v},
        }

    def to_env(self) -> dict[str, str]:
        """Render the release as desired-state env entries."""
        return {
            "OMNIGENT_IMAGE_REF": self.server_image_ref,
            "OMNIGENT_HOST_IMAGE_REF": self.host_image_refs.get("codex", ""),
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF": self.host_image_refs.get(
                "opencode", ""
            ),
            "OMNIGENT_SHARED_HOST_IMAGE_REF": self.host_image_refs.get("shared", ""),
            "OMNIGENT_PI_HOST_IMAGE_REF": self.host_image_refs.get("pi", ""),
        }

    def to_record(self) -> dict[str, Any]:
        """Render the release as the desired-state JSON sidecar document."""
        return {
            "revision": self.revision,
            "serverImageRef": self.server_image_ref,
            "hostImageRefs": dict(self.host_image_refs),
            "updatedAt": self.updated_at,
            "updatedBy": self.updated_by,
            "previous": copy.deepcopy(self.previous),
        }

    @staticmethod
    def from_record(record: Mapping[str, Any]) -> "OmnigentRelease | None":
        """Parse a sidecar release document; None when absent or malformed."""
        if not isinstance(record, Mapping):
            return None
        try:
            revision = int(record.get("revision", 0))
            server = str(record.get("serverImageRef") or "").strip()
            hosts = record.get("hostImageRefs") or {}
            if revision < 1 or not server or not isinstance(hosts, dict):
                return None
            return OmnigentRelease(
                revision=revision,
                server_image_ref=server,
                host_image_refs={
                    str(k): str(v or "").strip() for k, v in hosts.items()
                },
                updated_at=str(record.get("updatedAt") or ""),
                updated_by=str(record.get("updatedBy") or ""),
                previous=(
                    dict(record["previous"])
                    if isinstance(record.get("previous"), dict)
                    else None
                ),
            )
        except (TypeError, ValueError):
            return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def read_omnigent_release(
    env_entries: Mapping[str, str], record: Mapping[str, Any]
) -> OmnigentRelease | None:
    """Rebuild the recorded release from desired-state files.

    The JSON sidecar is authoritative for the revision chain; env entries
    must agree with it. A sidecar/env disagreement means an out-of-band edit
    happened, so this returns None and forces a converging migration instead
    of trusting either half.
    """
    raw = record.get(OMNIGENT_RELEASE_RECORD_KEY)
    if not isinstance(raw, dict):
        return None
    release = OmnigentRelease.from_record(raw)
    if release is None:
        return None
    expected_env = release.to_env()
    for key, value in expected_env.items():
        if not value:
            continue
        if str(env_entries.get(key) or "").strip() != value:
            return None
    return release


def _refs_agree(first: Mapping[str, str], second: Mapping[str, str]) -> bool:
    """Return whether two ref maps agree where both say something.

    The server ref must be present and equal on both sides; every other kind
    only constrains when both sides pin it. Live observations legitimately
    omit kinds the record pins (the resolved state has no legacy codex
    field), so a missing key is not a disagreement.
    """
    left = {k: str(v or "").strip() for k, v in first.items() if v}
    right = {k: str(v or "").strip() for k, v in second.items() if v}
    if not left.get("server") or left["server"] != right.get("server"):
        return False
    return all(left[k] == right[k] for k in set(left) & set(right) if k != "server")


def _candidate_supplies_new_refs(
    recorded: Mapping[str, str], candidate: Mapping[str, str]
) -> bool:
    """Return whether the candidate carries refs the record does not.

    Optional host families may be absent from an older record because they
    were unresolvable at the time. When later configuration or registry
    recovery supplies that host ref, the migration must persist it even
    though the symmetric intersection still agrees.
    """
    for kind, value in candidate.items():
        if kind == "server":
            continue
        wanted = str(value or "").strip()
        if not wanted:
            continue
        if str(recorded.get(kind) or "").strip() != wanted:
            # Missing or different: either a newly available authority or a
            # moved digest. Both require a new revision.
            if kind not in recorded or not str(recorded.get(kind) or "").strip():
                return True
    return False


def decide_release_transition(
    live_refs: Mapping[str, str],
    record: OmnigentRelease | None,
    candidate_refs: Mapping[str, str],
) -> tuple[str, dict[str, str]]:
    """Decide the migration action from live, recorded, and candidate refs.

    Returns ``(action, target_refs)`` where action is one of:

    - ``"noop"``: live, record, and candidates all agree; nothing to do.
    - ``"converge"``: the record exists but live disagrees (an interrupted
      migration or out-of-band container change); drive live to the record
      without advancing the revision.
    - ``"advance"``: live matches the record (or no record exists yet) but
      upstream offers new digests; cut a new revision for the candidates.
    """
    candidate = {k: str(v or "").strip() for k, v in candidate_refs.items() if v}
    live = {k: str(v or "").strip() for k, v in live_refs.items() if v}
    if record is not None:
        recorded = {k: v for k, v in record.refs().items() if v}
        if live and _refs_agree(live, recorded) and _refs_agree(recorded, candidate):
            # A candidate-only host ref (newly available authority absent from
            # the record) still requires a revision even though the symmetric
            # intersection agrees.
            if _candidate_supplies_new_refs(recorded, candidate):
                return "advance", candidate
            return "noop", recorded
        if not _refs_agree(live, recorded):
            return "converge", recorded
        if not _refs_agree(recorded, candidate) or _candidate_supplies_new_refs(
            recorded, candidate
        ):
            return "advance", candidate
        return "noop", recorded
    # First migration must establish the singular record even when live already
    # matches the candidate; otherwise Compose stays tag-driven and the
    # release-controller authority is never established.
    return "advance", dict(candidate)


@dataclass(frozen=True, slots=True)
class OmnigentReleaseDrivers:
    """Injectable boundaries for :func:`migrate_omnigent_release`.

    Production wiring uses the real Compose/DB/registry boundaries; tests
    inject fakes. Every driver is bounded by its own timeout or attempt
    budget so a stuck boundary surfaces as a step failure, never a hang.
    """

    deployment_inputs: Callable[[], Mapping[str, str]] = field(
        default=None, repr=False
    )
    resolve_candidates: Callable[
        [Mapping[str, str]], Awaitable[dict[str, str]]
    ] = field(default=None, repr=False)
    read_live_refs: Callable[[], Awaitable[dict[str, str]]] = field(
        default=None, repr=False
    )
    restart_server: Callable[[dict[str, str]], Awaitable[None]] = field(
        default=None, repr=False
    )
    await_resolution: Callable[[dict[str, str]], Awaitable[dict[str, str]]] = (
        field(default=None, repr=False)
    )
    sync_catalog: Callable[[], Awaitable[dict[str, Any]]] = field(
        default=None, repr=False
    )
    cut_policy_versions: Callable[
        [dict[str, str]], Awaitable[dict[str, list[str]]]
    ] = field(default=None, repr=False)
    refresh_schedules: Callable[[], Awaitable[int]] = field(
        default=None, repr=False
    )
    verify_live_container: Callable[[str], Awaitable[str | None]] = field(
        default=None, repr=False
    )


async def _default_deployment_inputs() -> Mapping[str, str]:
    import os

    return {
        key: str(os.environ.get(key) or "").strip()
        for key in OMNIGENT_RELEASE_INPUT_KEYS
        if str(os.environ.get(key) or "").strip()
    }


async def _default_resolve_candidates(
    env: Mapping[str, str],
) -> dict[str, str]:
    from api_service.services.omnigent_policies import (
        configured_bootstrap_image_refs,
        resolve_bootstrap_image_ref,
    )
    from moonmind.omnigent.bootstrap.image_resolution import resolve_omnigent_images

    state = await resolve_omnigent_images(dict(env))
    server_input, legacy_host_input = configured_bootstrap_image_refs(dict(env))
    # The release candidate must describe the upstream tag, not the currently
    # running server. resolve_omnigent_images deliberately reports the live
    # digest for mutable tags (so compatibility describes what Compose serves),
    # which would make the candidate equal live after every tag move and
    # prevent `advance`. Resolve the configured server input through the
    # acquisition boundary instead, falling back to the live-observed ref only
    # when the registry is temporarily unavailable.
    upstream_server_ref = await resolve_bootstrap_image_ref(server_input)
    legacy_host_ref = await resolve_bootstrap_image_ref(legacy_host_input)
    return {
        "server": str(
            upstream_server_ref or state.server_image_ref or ""
        ).strip(),
        "codex": str(legacy_host_ref or "").strip(),
        "opencode": str(state.opencode_host_image_ref or "").strip(),
        "shared": str(state.shared_host_image_ref or "").strip(),
        "pi": str(state.pi_host_image_ref or "").strip(),
    }


async def _default_read_live_refs() -> dict[str, str]:
    from moonmind.omnigent.bootstrap.store import load_resolved_state

    state = load_resolved_state()
    if state is None:
        return {}
    refs = {
        "server": str(state.server_image_ref or "").strip(),
        "opencode": str(state.opencode_host_image_ref or "").strip(),
        "shared": str(state.shared_host_image_ref or "").strip(),
        "pi": str(state.pi_host_image_ref or "").strip(),
    }
    return {k: v for k, v in refs.items() if v}


def _record_env(record_refs: Mapping[str, str]) -> dict[str, str]:
    return {
        "OMNIGENT_IMAGE_REF": str(record_refs.get("server") or ""),
        "OMNIGENT_HOST_IMAGE_REF": str(record_refs.get("codex") or ""),
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF": str(record_refs.get("opencode") or ""),
        "OMNIGENT_SHARED_HOST_IMAGE_REF": str(record_refs.get("shared") or ""),
        "OMNIGENT_PI_HOST_IMAGE_REF": str(record_refs.get("pi") or ""),
    }


async def _default_await_resolution(
    record_refs: Mapping[str, str], *, attempts: int = 20, delay_seconds: float = 15.0
) -> dict[str, str]:
    from moonmind.omnigent.bootstrap.image_resolution import resolve_omnigent_images
    from moonmind.omnigent.bootstrap.store import save_resolved_state

    # Digest-pinned inputs resolve by local inspection only: no registry pull,
    # so each attempt is cheap and the loop just waits for the restarted
    # container to become observable.
    env = _record_env(record_refs)
    last_error = "resolution never became ready"
    for _ in range(max(1, attempts)):
        state = await resolve_omnigent_images(dict(env))
        details = state.details if isinstance(state.details, dict) else {}
        compat = (
            details.get("opencodeHostCompatibility")
            if isinstance(details, dict)
            else None
        )
        observed = {
            "server": str(state.server_image_ref or "").strip(),
            "opencode": str(state.opencode_host_image_ref or "").strip(),
            "shared": str(state.shared_host_image_ref or "").strip(),
            "pi": str(state.pi_host_image_ref or "").strip(),
        }
        wanted = {k: v for k, v in record_refs.items() if v and k != "codex"}
        refs_match = all(observed.get(k) == v for k, v in wanted.items())
        # The opencode readiness verdict only gates deployments that run the
        # opencode host family; codex-only deployments match on refs alone.
        compat_ok = not wanted.get("opencode") or (
            isinstance(compat, dict) and compat.get("status") == "ready"
        )
        if refs_match and compat_ok:
            save_resolved_state(state)
            return {k: v for k, v in observed.items() if v}
        last_error = (
            f"status={compat.get('status') if isinstance(compat, dict) else '?'} "
            "failureCode="
            f"{compat.get('failureCode') if isinstance(compat, dict) else '?'}"
        )
        await asyncio.sleep(delay_seconds)
    raise OmnigentReleaseError("await-resolution", last_error)


async def _default_sync_catalog() -> dict[str, Any]:
    from api_service.db.base import get_async_session_context
    from api_service.services.omnigent_agent_profile_service import (
        synchronize_omnigent_harness_catalog,
    )

    async with get_async_session_context() as session:
        return dict(await synchronize_omnigent_harness_catalog(session))


def build_migrated_policy_document(
    document: Mapping[str, Any], *, server_ref: str, host_ref: str
) -> dict[str, Any]:
    """Return a copy of a policy document with only the image refs moved.

    Every other field (resources, boundaries, providers, rollout) is carried
    over verbatim so operator customizations survive the migration; only the
    two digest pins advance to the recorded release.
    """
    if not isinstance(document, dict):
        raise OmnigentReleaseError("cut-policies", "policy default has no document")
    host = document.get("host")
    if not isinstance(host, dict):
        raise OmnigentReleaseError(
            "cut-policies", "policy default has no host block"
        )
    if not server_ref or not host_ref:
        raise OmnigentReleaseError(
            "cut-policies", "record lacks images for this policy"
        )
    payload = copy.deepcopy(dict(document))
    payload["host"] = {**host, "serverImageRef": server_ref, "hostImageRef": host_ref}
    return payload


async def _default_cut_policy_versions(
    record_refs: Mapping[str, str], *, actor: str
) -> dict[str, list[str]]:
    """Cut one policy version per bootstrap policy whose images moved.

    The current default document is carried over verbatim except for the two
    image refs, so operator customizations survive the migration. The release
    is explicit operator-invoked authority, which is why it may advance even
    operator-owned defaults; the parent ref chain and cutover events record
    exactly what moved. Policies whose host family has no recorded ref (an
    unresolvable optional image) are skipped with a note instead of failing
    the migration.
    """
    from api_service.db.base import get_async_session_context
    from api_service.services.omnigent_policies import (
        OmnigentPolicy,
        OmnigentPolicyService,
        OmnigentPolicyVersion,
        PolicyDocument,
        PolicyState,
        _cutover_inactive_bootstrap_bindings,
    )
    from sqlalchemy import func, select

    cut: list[str] = []
    skipped: list[str] = []
    async with get_async_session_context() as session:
        service = OmnigentPolicyService(session)
        for policy_id, kind in OMNIGENT_RELEASE_POLICIES:
            policy = await session.get(OmnigentPolicy, policy_id)
            if policy is None or policy.default_version is None:
                continue
            current = await service.get_version(policy_id, policy.default_version)
            document = current.document_json
            if not isinstance(document, dict):
                raise OmnigentReleaseError(
                    "cut-policies", f"{policy_id} default has no document"
                )
            host = document.get("host")
            if not isinstance(host, dict):
                raise OmnigentReleaseError(
                    "cut-policies", f"{policy_id} default has no host block"
                )
            want_server = str(record_refs.get("server") or "")
            want_host = str(record_refs.get(kind) or "")
            if (
                host.get("serverImageRef") == want_server
                and host.get("hostImageRef") == want_host
            ):
                continue
            if not want_server or not want_host:
                skipped.append(policy_id)
                continue
            payload = build_migrated_policy_document(
                document, server_ref=want_server, host_ref=want_host
            )
            try:
                new_document = PolicyDocument.model_validate(payload)
            except ValueError as exc:
                raise OmnigentReleaseError(
                    "cut-policies", f"{policy_id} migrated document invalid: {exc}"
                ) from exc
            latest = (
                await session.execute(
                    select(func.max(OmnigentPolicyVersion.version)).where(
                        OmnigentPolicyVersion.policy_id == policy_id
                    )
                )
            ).scalar_one()
            row = await service.new_version(
                policy_id=policy_id,
                document=new_document,
                actor=actor,
                expected_parent_ref=f"{policy_id}@{latest}",
            )
            if not row.validation_json.get("valid"):
                raise OmnigentReleaseError(
                    "cut-policies", f"{policy_id}@{row.version} failed validation"
                )
            candidate = await service.transition(
                policy_id=policy_id,
                version=row.version,
                state=PolicyState.ACTIVE,
                actor=actor,
                make_default=True,
            )
            candidate_ref = f"{policy_id}@{candidate.version}"
            versions = await service.versions(policy_id)
            predecessors = tuple(
                f"{policy_id}@{item.version}"
                for item in versions
                if item.version != candidate.version
                and str(item.state) == PolicyState.ACTIVE.value
            )
            updated, _deferred = await _cutover_inactive_bootstrap_bindings(
                session,
                previous_refs=predecessors,
                policy_ref=candidate_ref,
            )
            service._event(
                policy_id,
                candidate.version,
                "bootstrap_authority_cutover",
                actor,
                {
                    "previousRefs": list(predecessors),
                    "policyRef": candidate_ref,
                    "serverImageRef": want_server,
                    "hostImageRef": want_host,
                    "updatedBindingCount": updated,
                    "releaseMigration": True,
                },
            )
            await session.commit()
            cut.append(candidate_ref)
    return {"cut": cut, "skipped": skipped}


async def _default_refresh_schedules() -> int:
    from api_service.db.base import get_async_session_context
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )

    try:
        async with get_async_session_context() as session:
            refreshed = await RecurringWorkflowsService(
                session
            ).refresh_managed_bootstrap_schedules(limit=500, raise_on_failure=True)
            await session.commit()
            return refreshed
    except OmnigentReleaseError:
        raise
    except Exception as exc:
        raise OmnigentReleaseError(
            "refresh-schedules",
            f"managed bootstrap schedule refresh failed: {exc}",
        ) from exc


async def _default_verify_live_container(server_ref: str) -> str | None:
    from moonmind.omnigent.bootstrap.image_resolution import (
        resolve_live_server_image_ref,
    )

    return await resolve_live_server_image_ref(server_ref)


def _default_drivers() -> OmnigentReleaseDrivers:
    return OmnigentReleaseDrivers(
        deployment_inputs=_default_deployment_inputs,
        resolve_candidates=_default_resolve_candidates,
        read_live_refs=_default_read_live_refs,
        restart_server=None,
        await_resolution=_default_await_resolution,
        sync_catalog=_default_sync_catalog,
        cut_policy_versions=None,
        refresh_schedules=_default_refresh_schedules,
        verify_live_container=_default_verify_live_container,
    )


def production_drivers(
    *,
    runner: Any,
    moonmind_image: str,
    actor: str,
    stack: str = "moonmind",
) -> OmnigentReleaseDrivers:
    """Wire :func:`migrate_omnigent_release` to the release controller.

    The runner recreates the omnigent server container onto the recorded
    digests (Compose renders them from the release-owned desired-state env
    file); policy cuts run under the release actor, which is explicit
    operator-invoked authority from ``update-moonmind.sh``.
    """

    async def _up_services(services: tuple[str, ...], phase: str) -> None:
        result = await runner.up(
            stack=stack,
            command=(
                "up",
                "-d",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                "300",
                *services,
            ),
            requested_image=moonmind_image,
        )
        # HostDockerComposeRunner reports process status as `exitCode`
        # (see deployment_execution._run_compose_command); accept every
        # historical key so a successful `compose up` is never misread as a
        # failure.
        raw_code = result.get(
            "exitCode", result.get("exit_code", result.get("returncode", 1))
        )
        try:
            code = int(raw_code)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            code = 1
        if code != 0:
            raise OmnigentReleaseError(
                phase,
                str((result.get("stderr") or result.get("stdout") or "")[:500])
                or f"compose up {' '.join(services)} failed",
            )

    async def restart_server(target: dict[str, str]) -> None:
        # The release record is already persisted to `.env.deploy` when this
        # runs. `omnigent` carries the new image, but `api` and
        # `temporal-worker-agent-runtime` were reconciled before the migration
        # and still carry the previous `OMNIGENT_*_REF` process values. Host
        # Class resolution prefers those stale values over the new shared
        # state and rejects them, so recreate every ref-consuming service
        # after persisting the record.
        await _up_services(("omnigent",), "restart-server")
        await _up_services(
            ("api", "temporal-worker-agent-runtime"),
            "restart-consumers",
        )

    async def cut_policy_versions(target: dict[str, str]) -> dict[str, list[str]]:
        return await _default_cut_policy_versions(target, actor=actor)

    base = _default_drivers()
    return OmnigentReleaseDrivers(
        deployment_inputs=base.deployment_inputs,
        resolve_candidates=base.resolve_candidates,
        read_live_refs=base.read_live_refs,
        restart_server=restart_server,
        await_resolution=base.await_resolution,
        sync_catalog=base.sync_catalog,
        cut_policy_versions=cut_policy_versions,
        refresh_schedules=base.refresh_schedules,
        verify_live_container=base.verify_live_container,
    )


async def migrate_omnigent_release(
    *,
    store: Any,
    runner: Any,
    owner: str = "system:deployment",
    moonmind_image: str = "",
    drivers: OmnigentReleaseDrivers | None = None,
    actor: str = "release",
) -> dict[str, Any]:
    """Advance the deployment to the resolved Omnigent release, or no-op.

    Every step is convergent: re-running after an interruption completes the
    pending work instead of duplicating it (record compare-and-set, idempotent
    container up, skip-when-current policy/schedule steps). Any failure raises
    :class:`OmnigentReleaseError` with the step name; the retained fleet owns
    recovery and the record's ``previous`` revision supports an explicit
    rollback through this same function.
    """
    from moonmind.omnigent.settings import build_omnigent_gate, generic_host_enabled

    if not build_omnigent_gate().enabled or not generic_host_enabled():
        return {"status": "skipped", "reason": "omnigent runtime not enabled"}

    # Hold the release-wide deployment lock through the migration so a second
    # queued release cannot rewrite the desired-state files while this
    # migration restarts Omnigent and cuts policies. The revision CAS below
    # still rejects any interleaving that slips through the gap between the
    # deployment update's lock release and this acquisition.
    import os

    lock_lease = None
    lock_dir = str(os.environ.get("MOONMIND_DEPLOYMENT_LOCK_DIR") or "").strip()
    if lock_dir:
        try:
            from moonmind.workflows.skills.deployment_execution import (
                FileDeploymentUpdateLockManager,
            )

            lock_lease = await FileDeploymentUpdateLockManager(
                lock_dir=lock_dir
            ).acquire("moonmind")
        except Exception as exc:
            raise OmnigentReleaseError(
                "conflict",
                f"could not acquire deployment lock for migration: {exc}",
            ) from exc
    try:
        return await _migrate_omnigent_release_inner(
            store=store,
            runner=runner,
            owner=owner,
            moonmind_image=moonmind_image,
            drivers=drivers,
            actor=actor,
        )
    finally:
        if lock_lease is not None:
            try:
                await lock_lease.release()
            except Exception:
                pass


async def _migrate_omnigent_release_inner(
    *,
    store: Any,
    runner: Any,
    owner: str = "system:deployment",
    moonmind_image: str = "",
    drivers: OmnigentReleaseDrivers | None = None,
    actor: str = "release",
) -> dict[str, Any]:
    run = drivers or _default_drivers()
    if run.restart_server is None or run.cut_policy_versions is None:
        raise OmnigentReleaseError(
            "wiring",
            "runner-bound drivers (restart_server, cut_policy_versions) are required",
        )

    env_entries, record_doc = store.read()
    record = read_omnigent_release(env_entries, record_doc)
    inputs = await run.deployment_inputs()
    candidates = await run.resolve_candidates(inputs)
    if not str(candidates.get("server") or "").strip():
        raise OmnigentReleaseError(
            "resolve-candidates", "could not resolve an omnigent server image"
        )
    live = await run.read_live_refs()
    action, target = decide_release_transition(live, record, candidates)

    if action == "noop":
        # A previous advance may have written the record and aligned the
        # server but failed before catalog, policy, schedule, or verification
        # finished. Rerun those convergent post-record steps before treating
        # matching refs as terminal; otherwise the receipt completes with
        # stale policies or schedules.
        assert record is not None
        try:
            resolved = await run.await_resolution(target)
            catalog = await run.sync_catalog()
            policy_outcome = await run.cut_policy_versions(target)
            refreshed = await run.refresh_schedules()
            live_digest = await run.verify_live_container(
                str(target.get("server") or "")
            )
        except OmnigentReleaseError:
            raise
        except Exception as exc:
            raise OmnigentReleaseError(
                "converge-post-steps",
                f"aligned refs still need post-record work: {exc}",
            ) from exc
        if live_digest and live_digest != str(target.get("server") or ""):
            raise OmnigentReleaseError(
                "verify",
                "running server does not carry the recorded digest",
            )
        if not live_digest:
            raise OmnigentReleaseError(
                "verify", "could not observe the running server image"
            )
        return {
            "status": "aligned",
            "revision": record.revision,
            "serverImageRef": target.get("server"),
            "policiesCut": policy_outcome["cut"],
            "policiesSkipped": policy_outcome["skipped"],
            "schedulesRefreshed": refreshed,
            "catalogRef": catalog.get("catalogRef"),
            "resolvedRefs": resolved,
        }

    if action == "advance":
        revision = (record.revision if record else 0) + 1
        new_release = OmnigentRelease(
            revision=revision,
            server_image_ref=str(target["server"]),
            host_image_refs={
                kind: str(target.get(kind) or "")
                for kind in OMNIGENT_RELEASE_HOST_KINDS
            },
            updated_at=_utc_now(),
            updated_by=owner,
            previous=record.to_record() if record else None,
        )
        # Revision compare-and-set: the file lock is released between the
        # deployment update and this migration, so a second release could have
        # advanced the record in between. Re-read and reject the write when
        # the revision moved instead of silently losing that revision.
        fresh_env, fresh_doc = store.read()
        fresh_record = read_omnigent_release(fresh_env, fresh_doc)
        fresh_revision = fresh_record.revision if fresh_record else 0
        expected_revision = record.revision if record else 0
        if fresh_revision != expected_revision:
            raise OmnigentReleaseError(
                "conflict",
                f"release record advanced concurrently "
                f"(expected r{expected_revision}, found r{fresh_revision}); "
                f"retained fleet owns recovery",
            )
        await store.merge(
            env_updates=new_release.to_env(),
            json_updates={OMNIGENT_RELEASE_RECORD_KEY: new_release.to_record()},
        )
        record = new_release
        target = dict(new_release.refs())

    # From here the target is the record: converge an interrupted migration
    # and finish a fresh advance through the same steps.
    await run.restart_server(target)
    resolved = await run.await_resolution(target)
    catalog = await run.sync_catalog()
    policy_outcome = await run.cut_policy_versions(target)
    refreshed = await run.refresh_schedules()
    live_digest = await run.verify_live_container(str(target.get("server") or ""))
    if live_digest and live_digest != str(target.get("server") or ""):
        # Compare by digest: the live check returns a repository digest for
        # the running container's image.
        raise OmnigentReleaseError(
            "verify",
            "running server does not carry the recorded digest",
        )
    if not live_digest:
        raise OmnigentReleaseError(
            "verify", "could not observe the running server image"
        )
    return {
        "status": "migrated" if action == "advance" else "converged",
        "revision": record.revision,
        "serverImageRef": target.get("server"),
        "hostImageRefs": {
            k: v for k, v in target.items() if k != "server" and v
        },
        "policiesCut": policy_outcome["cut"],
        "policiesSkipped": policy_outcome["skipped"],
        "schedulesRefreshed": refreshed,
        "catalogRef": catalog.get("catalogRef"),
        "resolvedRefs": resolved,
    }


__all__ = [
    "OMNIGENT_RELEASE_ENV_KEYS",
    "OMNIGENT_RELEASE_HOST_KINDS",
    "OMNIGENT_RELEASE_INPUT_KEYS",
    "OMNIGENT_RELEASE_POLICIES",
    "OMNIGENT_RELEASE_RECORD_KEY",
    "OmnigentRelease",
    "OmnigentReleaseDrivers",
    "OmnigentReleaseError",
    "build_migrated_policy_document",
    "decide_release_transition",
    "migrate_omnigent_release",
    "production_drivers",
    "read_omnigent_release",
]
