"""Issue #4349 R4: two credentials isolated across concurrent workflow runs.

A local workflow fixture stands in for two concurrent Temporal workflow
executions (no Temporal server is available in unit tests). Each workflow run
exercises the real production boundaries its Temporal counterpart uses:

- its own database session (independent storage connection),
- ``ProfileAuthProvider`` credential resolution with the per-profile
  ``(profile_id, key)`` cache (cache hits, refresh, rotation),
- ``ProviderProfileMaterializer`` + ``DatabaseSecretResolver`` for launch
  environment materialization (the same materializer the launcher uses),
- a per-profile capacity slot modeling ``max_parallel_runs`` execution
  leases with runtime-specific isolation (codex_cli vs claude_code).

Verifies two synthetic runtime/profile credentials stay isolated through
cache hits, refresh, rotation, and concurrent workflow runs, and that
revocation blocks only the affected workflow.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedSecret,
    SecretStatus,
)


def _factory(tmp_path, name="wfiso4349.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


def _provider_profile_row(profile_id, runtime_id, provider_id, secret_refs):
    # Production creation (`provider_profile_creation`) stores the canonical
    # derived isolation keys on the row; the launch boundary revalidates them.
    from moonmind.provider_profiles.isolation_policy import derive_isolation_policy

    policy = derive_isolation_policy(
        runtime_id=runtime_id,
        provider_id=provider_id,
        authentication_method="api_key",
        credential_source="secret_ref",
        runtime_materialization_mode="api_key_env",
    )
    assert policy is not None
    return ManagedAgentProviderProfile(
        profile_id=profile_id,
        runtime_id=runtime_id,
        provider_id=provider_id,
        credential_source="secret_ref",
        runtime_materialization_mode="api_key_env",
        secret_refs=secret_refs,
        clear_env_keys=list(policy.keys),
        credential_generation=1,
        enabled=True,
        auth_state="connected",
    )


class LocalCredentialWorkflow:
    """Local stand-in for one Temporal workflow run bound to one profile.

    Steps mirror the production launch path: admit a per-profile capacity
    slot (execution lease), resolve the credential through the profile
    provider on a private DB session, materialize the launch environment
    through the real materializer, then release the slot. The provider
    instance is retained across ``run`` calls so consecutive runs exercise
    the cache-hit path with live revision validation.
    """

    def __init__(self, session_factory, *, workflow_id, profile_id, slot):
        self._factory = session_factory
        self.workflow_id = workflow_id
        self.profile_id = profile_id
        self._slot = slot
        self.acquired_slots: list[tuple[str, str]] = []
        self._provider = None

    async def run(self, secret_key: str, env_key: str) -> dict:
        from moonmind.auth.profile_provider import ProfileAuthProvider
        from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
        from moonmind.workflows.adapters.materializer import (
            ProviderProfileMaterializer,
        )
        from moonmind.workflows.adapters.secret_boundary import (
            DatabaseSecretResolver,
        )

        async with self._slot:
            self.acquired_slots.append((self.profile_id, self.workflow_id))
            async with self._factory() as session:
                if self._provider is None:
                    self._provider = ProfileAuthProvider(session)
                # Rebind the retained provider (and its (profile_id, key)
                # cache) to the live session: cache entries carry
                # generation + revision validation, so reuse across runs
                # exercises the cache-hit path safely.
                self._provider.db = session
                value = await self._provider.get_secret_for_profile(
                    key=secret_key, profile_id=self.profile_id
                )
                if value is None:
                    return {"value": None, "env": {}, "cmd": []}
                profile = await session.get(
                    ManagedAgentProviderProfile, self.profile_id
                )
                assert profile is not None
                materializer = ProviderProfileMaterializer(
                    base_env={"PATH": "/usr/bin"},
                    secret_resolver=DatabaseSecretResolver(session),
                )
                runtime_profile = ManagedRuntimeProfile(
                    profile_id=profile.profile_id,
                    runtime_id=profile.runtime_id,
                    provider_id=profile.provider_id,
                    auth_mode="api_key",
                    credential_source="secret_ref",
                    runtime_materialization_mode="api_key_env",
                    auth_state="connected",
                    clear_env_keys=list(profile.clear_env_keys or []),
                    secret_refs=dict(profile.secret_refs or {}),
                    env_template={env_key: {"from_secret_ref": secret_key.lower()}},
                    env_overrides={},
                    file_templates=[],
                    command_template=[],
                )
                env, cmd = await materializer.materialize(runtime_profile)
                return {"value": value, "env": env, "cmd": cmd}


@pytest.mark.asyncio
async def test_two_credentials_isolated_across_concurrent_workflows(tmp_path):
    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            ManagedSecret(
                slug="wf-k1", ciphertext="wf-synthetic-1", status=SecretStatus.ACTIVE
            )
        )
        session.add(
            ManagedSecret(
                slug="wf-k2", ciphertext="wf-synthetic-2", status=SecretStatus.ACTIVE
            )
        )
        session.add(
            _provider_profile_row(
                "wf-p1", "codex_cli", "openai", {"openai_api_key": "db://wf-k1"}
            )
        )
        session.add(
            _provider_profile_row(
                "wf-p2", "claude_code", "anthropic", {"anthropic_api_key": "db://wf-k2"}
            )
        )
        await session.commit()

    slots = {"wf-p1": asyncio.Semaphore(1), "wf-p2": asyncio.Semaphore(1)}
    wf1 = LocalCredentialWorkflow(
        factory, workflow_id="wf-run-1", profile_id="wf-p1", slot=slots["wf-p1"]
    )
    wf2 = LocalCredentialWorkflow(
        factory, workflow_id="wf-run-2", profile_id="wf-p2", slot=slots["wf-p2"]
    )

    # Round 1: concurrent workflow runs resolve and materialize in isolation.
    r1, r2 = await asyncio.gather(
        wf1.run("OPENAI_API_KEY", "OPENAI_API_KEY"),
        wf2.run("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    assert r1["value"] == "wf-synthetic-1"
    assert r2["value"] == "wf-synthetic-2"
    assert r1["env"]["OPENAI_API_KEY"] == "wf-synthetic-1"
    assert r2["env"]["ANTHROPIC_API_KEY"] == "wf-synthetic-2"
    # Runtime-specific isolation: each workflow materialized its own runtime.
    assert r1["cmd"] == ["codex_cli"]
    assert r2["cmd"] == ["claude_code"]
    # Capacity slots: each workflow held only its own profile's slot.
    assert wf1.acquired_slots == [("wf-p1", "wf-run-1")]
    assert wf2.acquired_slots == [("wf-p2", "wf-run-2")]

    # Round 2: consecutive runs hit the retained per-profile cache and agree.
    r1b, r2b = await asyncio.gather(
        wf1.run("OPENAI_API_KEY", "OPENAI_API_KEY"),
        wf2.run("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    assert (r1b["value"], r2b["value"]) == ("wf-synthetic-1", "wf-synthetic-2")
    assert r1b["env"]["OPENAI_API_KEY"] == "wf-synthetic-1"
    assert r2b["env"]["ANTHROPIC_API_KEY"] == "wf-synthetic-2"

    # Rotation: wf-p1's secret value rotates (revision + generation bump)
    # while wf-p2 is untouched; concurrent runs observe exactly that.
    async with factory() as session:
        secret = (
            await session.execute(
                select(ManagedSecret).where(ManagedSecret.slug == "wf-k1")
            )
        ).scalar_one()
        secret.ciphertext = "wf-synthetic-1-rotated"
        secret.credential_revision = int(secret.credential_revision or 1) + 1
        profile = await session.get(ManagedAgentProviderProfile, "wf-p1")
        assert profile is not None
        profile.credential_generation = int(profile.credential_generation or 1) + 1
        await session.commit()

    r1c, r2c = await asyncio.gather(
        wf1.run("OPENAI_API_KEY", "OPENAI_API_KEY"),
        wf2.run("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    assert r1c["value"] == "wf-synthetic-1-rotated"
    assert r1c["env"]["OPENAI_API_KEY"] == "wf-synthetic-1-rotated"
    assert r2c["value"] == "wf-synthetic-2"
    assert r2c["env"]["ANTHROPIC_API_KEY"] == "wf-synthetic-2"

    # Revocation blocks only the affected workflow; the other still resolves.
    async with factory() as session:
        secret2 = (
            await session.execute(
                select(ManagedSecret).where(ManagedSecret.slug == "wf-k2")
            )
        ).scalar_one()
        secret2.status = SecretStatus.DISABLED
        await session.commit()

    r1d, r2d = await asyncio.gather(
        wf1.run("OPENAI_API_KEY", "OPENAI_API_KEY"),
        wf2.run("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    assert r1d["value"] == "wf-synthetic-1-rotated"
    assert r1d["env"]["OPENAI_API_KEY"] == "wf-synthetic-1-rotated"
    assert r2d["value"] is None
