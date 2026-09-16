"""Request-level accounts lifecycle tests for #4122 (parent #4116).

Proves the HTTP boundary in ``api_service/api/routers/accounts_4122.py``
against real tables on SQLite through the ASGI app: the fresh-install
setup -> login -> invite -> second-user enrollment journey, denial
matrix (unauthenticated setup, expired/replayed invites, non-admin
escalation, open registration absent), disable/reset/role enforcement
on live sessions, last-admin protection with operator recovery,
password change/logout semantics, refresh-surface rejection, abuse
rate limiting, CSRF/origin enforcement, and no reusable auth material
in browser JSON.

Every authenticated call goes through the real session-cookie
principal (no dependency overrides for identity): ``httpx`` clients
hold their cookies, so generation bumps, revocation, and flag checks
are exercised end to end.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import moonmind.security.auth_modes_4120 as auth_modes
from api_service import auth_providers
from api_service.api.routers import accounts_4122 as accounts_router
from api_service.db import base as db_base
from api_service.db.models import Base, User
from api_service.main import app
from moonmind.security import omnigent_auth_qualification as q
from moonmind.security.account_lifecycle_4122 import (
    mint_bootstrap_capability,
    mint_invite,
    mint_recovery_capability,
)

NOW = 1_700_000_000.0
OWNER = "owner@example.com"
MEMBER = "member@example.com"
PASSWORD = "correct-horse-4122-battery"


def _test_session_config() -> q.MoonmindAuthConfig:
    return q.MoonmindAuthConfig(
        mode="accounts",
        cookie_name=q.MOONMIND_DEV_COOKIE,
        cookie_secret=b"t" * 32,
        session_ttl_seconds=3600,
        require_secure_cookies=False,
    )


@pytest.fixture(scope="module")
def _module_db(tmp_path_factory):
    import asyncio as _asyncio

    tmp = tmp_path_factory.mktemp("accounts-4122-db")
    db_url = f"sqlite+aiosqlite:///{tmp}/accounts-4122.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, maker

    async def _teardown(engine):
        await engine.dispose()

    engine, maker = _asyncio.run(_setup())
    _orig = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
    db_base.DATABASE_URL = db_url
    db_base.engine = engine
    db_base.async_session_maker = maker
    yield maker
    db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = _orig
    _asyncio.run(_teardown(engine))


@pytest.fixture(autouse=True)
def _accounts_env(_module_db, monkeypatch):
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", "accounts")
    monkeypatch.setattr(
        auth_providers,
        "build_moonmind_control_plane_config",
        lambda *args, **kwargs: _test_session_config(),
    )
    monkeypatch.setenv("MOONMIND_ACCOUNTS_KEY", "z" * 32)
    monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
    accounts_router._accounts_rate_limiter = accounts_router._AccountsRateLimiter()

    async def _override_session():
        async with _module_db() as session:
            yield session

    from api_service.db.base import get_async_session

    app.dependency_overrides[get_async_session] = _override_session
    yield
    app.dependency_overrides.clear()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _setup_owner(client: AsyncClient, login: str = OWNER) -> None:
    # The router resolves its key from MOONMIND_ACCOUNTS_KEY ("z"*32);
    # mint here with the same operator key the router verifies with.
    token = mint_bootstrap_capability(login, key=b"z" * 32)
    resp = await client.post(
        "/api/v1/accounts/setup",
        json={"login": login, "password": PASSWORD, "bootstrap_token": token},
    )
    assert resp.status_code == 201, resp.text


async def _flags(login: str) -> tuple[bool, bool]:
    maker = db_base.async_session_maker
    async with maker() as session:
        from sqlalchemy import select

        result = await session.execute(select(User).where(User.email == login))
        user = result.scalars().first()
        assert user is not None
        return bool(user.is_active), bool(user.is_superuser)


# ---------------------------------------------------------------------------
# Fresh-install journey
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_install_journey_setup_login_invite_enroll():
    async with _client() as client:
        await _setup_owner(client)
        assert "token" not in (await client.get("/api/v1/accounts/me")).text.lower()

        me = await client.get("/api/v1/accounts/me")
        assert me.status_code == 200, me.text
        body = me.json()
        assert body["login"] == OWNER and body["is_superuser"] is True

        invite = await client.post("/api/v1/accounts/invites", json={"login": MEMBER})
        assert invite.status_code == 201, invite.text
        invite_token = invite.json()["invite_token"]

    async with _client() as second:
        enroll = await second.post(
            "/api/v1/accounts/enroll",
            json={"login": MEMBER, "password": PASSWORD, "invite_token": invite_token},
        )
        assert enroll.status_code == 201, enroll.text
        assert "Set-Cookie" in enroll.headers
        assert "invite_token" not in enroll.text

        status = await second.get("/api/v1/accounts/me")
        assert status.status_code == 200
        assert status.json()["is_superuser"] is False

        login = await second.post(
            "/api/v1/accounts/login", json={"login": MEMBER, "password": PASSWORD}
        )
        assert login.status_code == 200
        assert "Set-Cookie" in login.headers
        assert "Cache-Control" in login.headers


@pytest.mark.asyncio
async def test_setup_closed_after_first_owner():
    async with _client() as client:
        await _setup_owner(client, login="solo@example.com")
        token = mint_bootstrap_capability("late@example.com", key=b"z" * 32)
        resp = await client.post(
            "/api/v1/accounts/setup",
            json={"login": "late@example.com", "password": PASSWORD, "bootstrap_token": token},
        )
        assert resp.status_code == 401
        assert resp.json() == {"code": "auth_invalid"}


@pytest.mark.asyncio
async def test_setup_without_operator_authority_denied():
    async with _client() as client:
        resp = await client.post(
            "/api/v1/accounts/setup",
            json={"login": "nobody@example.com", "password": PASSWORD, "bootstrap_token": "bogus"},
        )
        assert resp.status_code == 401
        assert resp.json() == {"code": "auth_invalid"}


@pytest.mark.asyncio
async def test_no_open_registration_route():
    async with _client() as client:
        for path in ("/api/v1/accounts/register", "/api/v1/auth/register", "/api/auth/register"):
            resp = await client.post(path, json={"login": "x", "password": PASSWORD})
            assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unknown_login_matches_wrong_password_error():
    async with _client() as client:
        await _setup_owner(client, login="known4122a@example.com")
        unknown = await client.post(
            "/api/v1/accounts/login", json={"login": "ghost@example.com", "password": PASSWORD}
        )
        wrong = await client.post(
            "/api/v1/accounts/login",
            json={"login": "known4122a@example.com", "password": "wrong-password-xyz-1"},
        )
        assert unknown.status_code == 401 and wrong.status_code == 401
        assert unknown.json() == wrong.json() == {"code": "auth_invalid"}


@pytest.mark.asyncio
async def test_expired_and_replayed_invites_denied():
    async with _client() as client:
        await _setup_owner(client, login="inviter4122@example.com")

        expired = mint_invite("expired4122@example.com", key=b"z" * 32, ttl_seconds=60, now=NOW - 3600)
        resp = await client.post(
            "/api/v1/accounts/enroll",
            json={"login": "expired4122@example.com", "password": PASSWORD, "invite_token": expired},
        )
        assert resp.status_code == 401

        invite = await client.post("/api/v1/accounts/invites", json={"login": "once4122@example.com"})
        assert invite.status_code == 201, invite.text
        token = invite.json()["invite_token"]
    async with _client() as second:
        first = await second.post(
            "/api/v1/accounts/enroll",
            json={"login": "once4122@example.com", "password": PASSWORD, "invite_token": token},
        )
        assert first.status_code == 201
        replay = await second.post(
            "/api/v1/accounts/enroll",
            json={"login": "once4122@example.com", "password": PASSWORD, "invite_token": token},
        )
        assert replay.status_code in (401, 409)


@pytest.mark.asyncio
async def test_non_admin_cannot_create_invites_or_manage():
    async with _client() as client:
        await _setup_owner(client, login="admin4122@example.com")
        invite = await client.post("/api/v1/accounts/invites", json={"login": "plain4122@example.com"})
        assert invite.status_code == 201, invite.text
        token = invite.json()["invite_token"]
    async with _client() as second:
        enrolled = await second.post(
            "/api/v1/accounts/enroll",
            json={"login": "plain4122@example.com", "password": PASSWORD, "invite_token": token},
        )
        assert enrolled.status_code == 201
        denied_invite = await second.post("/api/v1/accounts/invites", json={"login": "evil@example.com"})
        assert denied_invite.status_code == 403
        denied_action = await second.post(
            "/api/v1/accounts/members/action",
            json={"target_login": "admin4122@example.com", "action": "revoke_admin"},
        )
        assert denied_action.status_code == 403


@pytest.mark.asyncio
async def test_disable_enforced_on_sessions_and_login():
    async with _client() as client:
        await _setup_owner(client, login="boss4122@example.com")
        invite = await client.post("/api/v1/accounts/invites", json={"login": "worker4122@example.com"})
        token = invite.json()["invite_token"]
    async with _client() as worker:
        enrolled = await worker.post(
            "/api/v1/accounts/enroll",
            json={"login": "worker4122@example.com", "password": PASSWORD, "invite_token": token},
        )
        assert enrolled.status_code == 201
    async with _client() as client:
        action = await client.post(
            "/api/v1/accounts/members/action",
            json={"target_login": "worker4122@example.com", "action": "grant_admin"},
        )
        assert action.status_code == 200
        assert action.json()["is_superuser"] is True
        revoke = await client.post(
            "/api/v1/accounts/members/action",
            json={"target_login": "worker4122@example.com", "action": "revoke_admin"},
        )
        assert revoke.status_code == 200
        assert revoke.json()["is_superuser"] is False
        action = await client.post(
            "/api/v1/accounts/members/action",
            json={"target_login": "worker4122@example.com", "action": "deactivate"},
        )
        assert action.status_code == 200
        members = await client.get("/api/v1/accounts/members")
        flagged = {m["login"]: m for m in members.json()["members"]}
        assert flagged["worker4122@example.com"]["is_active"] is False
    async with _client() as worker:
        # Cached session no longer validates.
        me = await worker.get("/api/v1/accounts/me")
        assert me.status_code in (401, 403)
        # Concurrent login after disable receives no usable session.
        login = await worker.post(
            "/api/v1/accounts/login",
            json={"login": "worker4122@example.com", "password": PASSWORD},
        )
        assert login.status_code == 403
        assert "Set-Cookie" not in login.headers


@pytest.mark.asyncio
async def test_last_admin_protected_and_recovery_restores():
    async with _client() as client:
        await _setup_owner(client, login="last4122@example.com")
        refused = await client.post(
            "/api/v1/accounts/members/action",
            json={"target_login": "last4122@example.com", "action": "revoke_admin"},
        )
        assert refused.status_code == 403
        assert refused.json() == {"code": "last_admin_protected"}

        recovery_token = mint_recovery_capability("last4122@example.com", key=b"z" * 32)
        redeemed = await client.post(
            "/api/v1/accounts/recovery/redeem",
            json={
                "login": "last4122@example.com",
                "new_password": "brand-new-password-4122",
                "recovery_token": recovery_token,
            },
        )
        assert redeemed.status_code == 200, redeemed.text
        assert await _flags("last4122@example.com") == (True, True)

        replay = await client.post(
            "/api/v1/accounts/recovery/redeem",
            json={
                "login": "last4122@example.com",
                "new_password": "brand-new-password-4122",
                "recovery_token": recovery_token,
            },
        )
        assert replay.status_code == 401

        login = await client.post(
            "/api/v1/accounts/login",
            json={"login": "last4122@example.com", "password": "brand-new-password-4122"},
        )
        assert login.status_code == 200


@pytest.mark.asyncio
async def test_password_change_invalidates_other_sessions():
    async with _client() as first, _client() as second:
        token = mint_bootstrap_capability("rotate4122@example.com", key=b"z" * 32)
        resp = await first.post(
            "/api/v1/accounts/setup",
            json={"login": "rotate4122@example.com", "password": PASSWORD, "bootstrap_token": token},
        )
        assert resp.status_code == 201
        login2 = await second.post(
            "/api/v1/accounts/login",
            json={"login": "rotate4122@example.com", "password": PASSWORD},
        )
        assert login2.status_code == 200

        changed = await first.post(
            "/api/v1/accounts/password/change",
            json={"current_password": PASSWORD, "new_password": "rotated-password-4122-x"},
        )
        assert changed.status_code == 200, changed.text
        assert "Set-Cookie" in changed.headers

        me = await first.get("/api/v1/accounts/me")
        assert me.status_code == 200
        stale = await second.get("/api/v1/accounts/me")
        assert stale.status_code in (401, 403)
    # The new hash persisted: old password fails, new password works.
    async with _client() as third:
        old = await third.post(
            "/api/v1/accounts/login",
            json={"login": "rotate4122@example.com", "password": PASSWORD},
        )
        assert old.status_code == 401
        new = await third.post(
            "/api/v1/accounts/login",
            json={"login": "rotate4122@example.com", "password": "rotated-password-4122-x"},
        )
        assert new.status_code == 200


@pytest.mark.asyncio
async def test_logout_clears_session_only():
    async with _client() as client:
        await _setup_owner(client, login="logout4122@example.com")
        out = await client.post("/api/v1/accounts/logout")
        assert out.status_code == 200
        assert "Set-Cookie" in out.headers
        me = await client.get("/api/v1/accounts/me")
        assert me.status_code == 401


@pytest.mark.asyncio
async def test_refresh_surface_rejected_and_rate_limited():
    async with _client() as client:
        await _setup_owner(client, login="surface4122@example.com")
        refresh = await client.post(
            "/api/v1/accounts/login",
            json={"login": "surface4122@example.com", "password": PASSWORD, "refresh_token": "x"},
        )
        assert refresh.status_code == 401

        for _ in range(20):
            await client.post(
                "/api/v1/accounts/login",
                json={"login": "surface4122@example.com", "password": "wrong-password-xyz-1"},
            )
        limited = await client.post(
            "/api/v1/accounts/login",
            json={"login": "surface4122@example.com", "password": "wrong-password-xyz-1"},
        )
        assert limited.status_code == 429
        assert limited.json() == {"code": "rate_limited"}


@pytest.mark.asyncio
async def test_incompatible_hash_requires_enrollment_and_weak_rejected():
    maker = db_base.async_session_maker
    async with maker() as session:
        session.add(
            User(
                email="legacy4122@example.com",
                hashed_password="not-argon2-legacy",
                is_active=True,
                is_superuser=False,
                is_verified=False,
            )
        )
        await session.commit()
    async with _client() as client:
        resp = await client.post(
            "/api/v1/accounts/login",
            json={"login": "legacy4122@example.com", "password": "whatever-password-1"},
        )
        assert resp.status_code == 403
        assert resp.json() == {"code": "enrollment_required"}

        token = mint_bootstrap_capability("weak4122@example.com", key=b"z" * 32)
        weak = await client.post(
            "/api/v1/accounts/setup",
            json={"login": "weak4122@example.com", "password": "short", "bootstrap_token": token},
        )
        assert weak.status_code == 422


@pytest.mark.asyncio
async def test_non_accounts_mode_not_advertised(monkeypatch):
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", "oidc")
    async with _client() as client:
        resp = await client.post(
            "/api/v1/accounts/login", json={"login": "x", "password": "y"}
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_csrf_origin_enforced_for_cookie_mutations(monkeypatch):
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "http://testserver")
    async with _client() as client:
        await _setup_owner(client, login="csrf4122@example.com")
        # Cookie-less API login passes without an Origin header.
        fresh = _client()
        async with fresh:
            login = await fresh.post(
                "/api/v1/accounts/login",
                json={"login": "csrf4122@example.com", "password": PASSWORD},
            )
            assert login.status_code == 200, login.text
        # A cookie-present cross-origin mutation is rejected and the
        # session survives the rejected logout.
        evil = await client.post(
            "/api/v1/accounts/logout", headers={"Origin": "https://evil.example"}
        )
        assert evil.status_code == 401
        me = await client.get("/api/v1/accounts/me")
        assert me.status_code == 200
        ok = await client.post(
            "/api/v1/accounts/logout", headers={"Origin": "http://testserver"}
        )
        assert ok.status_code == 200


@pytest.mark.asyncio
async def test_browser_json_carries_no_session_material():
    async with _client() as client:
        await _setup_owner(client, login="leak4122@example.com")
        me = await client.get("/api/v1/accounts/me")
        payload = me.json()
        rendered = str(payload).lower()
        assert "hashed_password" not in rendered
        assert "invite_token" not in rendered
        assert "recovery_token" not in rendered
        assert "refresh_token" not in rendered


def test_cli_mint_commands_print_single_use_capabilities():
    from typer.testing import CliRunner

    from moonmind.cli import app as cli_app

    runner = CliRunner()
    bootstrap = runner.invoke(cli_app, ["accounts", "mint-bootstrap", "op@example.com"])
    assert bootstrap.exit_code == 0, bootstrap.output
    recovery = runner.invoke(cli_app, ["accounts", "mint-recovery", "op@example.com"])
    assert recovery.exit_code == 0, recovery.output
    assert bootstrap.output.strip() != recovery.output.strip()
    # Capability material is printed once for operator delivery; the
    # key itself is never echoed.
    assert "z" * 32 not in bootstrap.output
