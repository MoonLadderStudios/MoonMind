"""MM-713 local-first Settings smoke test.

Drives the local-first walkthrough described in
`docs/Security/SettingsLocalFirstBringUp.md` end-to-end against a freshly
created sqlite database and the real `api_service.main.app`. The test runs
under the required `integration_ci` suite so any regression in the minimum
local-first path is caught on every PR.

The smoke test intentionally exercises only:

1. The empty-state catalog (no overrides yet).
2. Managed-secret creation.
3. A SecretRef-backed Settings override.
4. The cross-check via the secret-usage API.
5. Reset returning to inheritance.

Anything beyond the smallest path through the Settings system is covered
by other suites (see §9 of the walkthrough doc).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedSecret,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)
from api_service.main import app

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
]

SETTINGS_USER_DEP = get_current_user()

# The SecretRef that the local-first operator creates. The plaintext that
# we POST never appears in any later response or persisted row.
LOCAL_SECRET_SLUG = "github-pat-local"
LOCAL_SECRET_REF = f"db://{LOCAL_SECRET_SLUG}"
PLACEHOLDER_PLAINTEXT = "ghp_local_first_plaintext_only_in_request"
TOKEN_KEY = "integrations.github.token_ref"


@pytest_asyncio.fixture
async def local_first_db(tmp_path):
    """Freshly bootstrapped sqlite session — emulates a clean local deploy."""

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/settings-local-first-smoke.db"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    original = db_base.async_session_maker
    db_base.async_session_maker = session_maker

    user = SimpleNamespace(
        id=uuid4(),
        email="local-first-smoke@example.com",
        is_superuser=True,
        settings_permissions={
            "settings.catalog.read",
            "settings.effective.read",
            "settings.user.write",
            "settings.workspace.write",
            "secrets.metadata.read",
            "secrets.value.write",
        },
        workspace_id=uuid4(),
    )
    app.dependency_overrides[SETTINGS_USER_DEP] = lambda: user
    try:
        yield session_maker
    finally:
        app.dependency_overrides.pop(SETTINGS_USER_DEP, None)
        db_base.async_session_maker = original
        await engine.dispose()


async def test_local_first_walkthrough_runs_end_to_end(local_first_db):
    """Walkthrough §1–§5 must succeed against a clean local install."""

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # --- §1 Empty-state catalog read (SettingsSystem.md §5.1, §10, §29.8) ---
        catalog = await client.get(
            "/api/v1/settings/catalog",
            params={"section": "user-workspace", "scope": "workspace"},
        )
        assert catalog.status_code == 200
        catalog_body = catalog.json()
        token_descriptor = next(
            (
                descriptor
                for descriptors in catalog_body["categories"].values()
                for descriptor in descriptors
                if descriptor["key"] == TOKEN_KEY
            ),
            None,
        )
        # On a clean install, the SecretRef descriptor lives in Providers
        # & Secrets; the user-workspace catalog need not expose it. Confirm
        # that *no* descriptor leaks plaintext-shaped keys regardless.
        for descriptors in catalog_body["categories"].values():
            for descriptor in descriptors:
                assert "plaintext" not in descriptor
                assert "ciphertext" not in descriptor
                assert "resolved_value" not in descriptor
                assert descriptor["source"] in {
                    "default",
                    "config_file",
                    "environment",
                }
                assert descriptor["source_explanation"]

        # --- §2 Managed secret creation (§14.1, §22.2) ---
        created = await client.post(
            "/api/v1/secrets",
            json={"slug": LOCAL_SECRET_SLUG, "plaintext": PLACEHOLDER_PLAINTEXT},
        )
        assert created.status_code == 201, created.text
        created_body = created.json()
        assert created_body["slug"] == LOCAL_SECRET_SLUG
        assert created_body["secretRef"] == LOCAL_SECRET_REF
        # The plaintext we sent must never come back in the response body.
        assert PLACEHOLDER_PLAINTEXT not in created.text

        # --- §3 SecretRef-backed override (§5.3, §10.4, §29.5, §29.6) ---
        bound = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {TOKEN_KEY: LOCAL_SECRET_REF},
                "expected_versions": {TOKEN_KEY: 1},
                "reason": "Local-first smoke wiring",
            },
        )
        assert bound.status_code == 200, bound.text
        bound_body = bound.json()
        token_value = bound_body["values"][TOKEN_KEY]
        # A SecretRef descriptor backed by a resolvable `db://` reference is
        # canonicalized to `secret_ref` per SettingsSystem.md §7.6. Plain
        # `workspace_override` is the source for a SecretRef descriptor with
        # a non-`db://` reference (e.g. `env://`). Either is valid evidence
        # that the override row was recorded; the invariant in §29.8 is that
        # *a* source is always reported.
        assert token_value["source"] in {"secret_ref", "workspace_override"}
        assert token_value["value"] == LOCAL_SECRET_REF
        change_events = bound_body["change_events"]
        assert any(
            event["event_type"] == "setting_changed" and event["key"] == TOKEN_KEY
            for event in change_events
        )
        # The plaintext never crosses the settings boundary.
        assert PLACEHOLDER_PLAINTEXT not in bound.text

        # --- §4 Cross-check via secret usage view (§14.4) ---
        usage = await client.get(f"/api/v1/secrets/{LOCAL_SECRET_SLUG}/usage")
        assert usage.status_code == 200, usage.text
        usage_body = usage.json()
        assert usage_body["secretRef"] == LOCAL_SECRET_REF
        assert any(
            consumer["settingKey"] == TOKEN_KEY
            and consumer["scope"] == "workspace"
            and consumer["consumerType"] == "setting_override"
            for consumer in usage_body["usages"]
        )
        assert PLACEHOLDER_PLAINTEXT not in usage.text

        # --- §5 Reset returns to inheritance (§5.4, §29.9) ---
        reset = await client.delete(f"/api/v1/settings/workspace/{TOKEN_KEY}")
        assert reset.status_code == 200, reset.text
        reset_body = reset.json()
        # After reset the SecretRef descriptor falls back to inherited values
        # (default/config/environment) — never to a SecretRef-typed source.
        assert reset_body["source"] in {"default", "config_file", "environment"}
        assert reset_body["source"] != "secret_ref"

    # --- Persistence-level guarantees (§22.1, §22.8, §29.4) ---
    async with local_first_db() as session:
        leftover = (
            await session.execute(
                select(SettingsOverride).where(SettingsOverride.key == TOKEN_KEY)
            )
        ).scalars().all()
        assert leftover == [], "Reset must remove the override row, not mutate defaults."

        audit_rows = (
            await session.execute(
                select(SettingsAuditEvent)
                .where(SettingsAuditEvent.key == TOKEN_KEY)
                .order_by(SettingsAuditEvent.created_at)
            )
        ).scalars().all()
        assert audit_rows, "Settings changes must be audited."
        for row in audit_rows:
            # Audit policy for `integrations.github.token_ref` declares
            # `redact=True`. SecretRef descriptors are allowed to keep the
            # reference value for usage-trace reasons, but never plaintext.
            assert row.redacted is True
            for payload in (row.old_value_json, row.new_value_json):
                if isinstance(payload, str):
                    assert PLACEHOLDER_PLAINTEXT not in payload

        secret_rows = (
            await session.execute(
                select(ManagedSecret).where(ManagedSecret.slug == LOCAL_SECRET_SLUG)
            )
        ).scalars().all()
        assert len(secret_rows) == 1
        assert secret_rows[0].status == SecretStatus.ACTIVE


async def test_local_first_catalog_loads_with_no_persisted_overrides(local_first_db):
    """An operator with an empty database must be able to read the catalog
    for every section without errors — the local-first baseline (§3 goal 10).
    """

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for section in ("user-workspace", "providers-secrets", "operations"):
            response = await client.get(
                "/api/v1/settings/catalog", params={"section": section}
            )
            assert response.status_code == 200, response.text
            categories = response.json()["categories"]
            # Even on a fresh install every catalog section returns at
            # least one descriptor or an empty container — but never an
            # error or a plaintext leak.
            assert isinstance(categories, dict)
            for descriptors in categories.values():
                for descriptor in descriptors:
                    assert "plaintext" not in descriptor
                    assert "ciphertext" not in descriptor
                    assert descriptor.get("source_explanation")
