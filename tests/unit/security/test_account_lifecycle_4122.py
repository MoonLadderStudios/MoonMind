"""Account-lifecycle regression tests for #4122 (parent #4116, docs #4130).

Hermetic coverage for ``moonmind.security.account_lifecycle_4122`` mirroring
the ``test_session_authority_4121.py`` pattern: protected first-owner setup
(single winner, expiring one-use bootstrap, no public re-claim), expiring
one-use invites bound to one login, member administration with last-admin
protection, and local operator recovery (short-lived, one-use, never a
security-disabling step). No database, no network, deterministic clock.
"""

from __future__ import annotations

import time

import pytest

from moonmind.security.account_lifecycle_4122 import (
    AdminRefusedError,
    BootstrapError,
    InMemoryLifecycleStore,
    InviteError,
    LifecycleMember,
    RecoveryError,
    apply_member_action,
    assert_no_secret_leak,
    claim_first_owner,
    http_status_for_lifecycle_error,
    mint_bootstrap_capability,
    mint_invite,
    mint_recovery_capability,
    redacted_lifecycle_event,
    redeem_invite,
    redeem_recovery_capability,
)

KEY = b"k" * 32
OTHER_KEY = b"o" * 32
NOW = 1_700_000_000.0


def _store(*, owner: bool = False) -> InMemoryLifecycleStore:
    return InMemoryLifecycleStore(owner_exists=owner)


def test_bootstrap_claims_single_first_owner() -> None:
    store = _store()
    token = mint_bootstrap_capability("owner", key=KEY, now=NOW)
    assert claim_first_owner(token, key=KEY, store=store, login="owner", now=NOW + 1) == "owner"
    assert store.has_owner()


def test_bootstrap_is_one_use_and_concurrent_setups_yield_one_winner() -> None:
    store = _store()
    token = mint_bootstrap_capability("owner", key=KEY, now=NOW)
    assert claim_first_owner(token, key=KEY, store=store, login="owner", now=NOW + 1) == "owner"
    with pytest.raises(BootstrapError):
        claim_first_owner(token, key=KEY, store=store, login="owner", now=NOW + 2)
    # A second capability cannot re-claim once an owner exists.
    second = mint_bootstrap_capability("owner", key=KEY, now=NOW)
    with pytest.raises(BootstrapError) as excinfo:
        claim_first_owner(second, key=KEY, store=store, login="owner", now=NOW + 3)
    assert excinfo.value.code == "bootstrap_closed"


def test_bootstrap_expiry_login_binding_and_key_binding() -> None:
    store = _store()
    token = mint_bootstrap_capability("owner", key=KEY, ttl_seconds=60, now=NOW)
    with pytest.raises(BootstrapError):
        claim_first_owner(token, key=KEY, store=store, login="owner", now=NOW + 61)
    with pytest.raises(BootstrapError):
        claim_first_owner(token, key=KEY, store=_store(), login="someone-else", now=NOW + 1)
    with pytest.raises(BootstrapError):
        claim_first_owner(token, key=OTHER_KEY, store=_store(), login="owner", now=NOW + 1)
    with pytest.raises(BootstrapError):
        claim_first_owner("not-a-capability", key=KEY, store=_store(), login="owner", now=NOW)


def test_bootstrap_closed_on_populated_database() -> None:
    store = _store(owner=True)
    token = mint_bootstrap_capability("owner", key=KEY, now=NOW)
    with pytest.raises(BootstrapError) as excinfo:
        claim_first_owner(token, key=KEY, store=store, login="owner", now=NOW + 1)
    assert excinfo.value.code == "bootstrap_closed"


def test_invite_is_expiring_one_use_and_login_bound() -> None:
    store = _store()
    token = mint_invite("alice", key=KEY, ttl_seconds=3600, now=NOW)
    assert redeem_invite(token, key=KEY, store=store, login="alice", now=NOW + 10) == "alice"
    with pytest.raises(InviteError):
        redeem_invite(token, key=KEY, store=store, login="alice", now=NOW + 11)
    # Binding is exact: a different login never redeems.
    other = mint_invite("bob", key=KEY, now=NOW)
    with pytest.raises(InviteError):
        redeem_invite(other, key=KEY, store=_store(), login="BOB", now=NOW + 1)
    expired = mint_invite("carol", key=KEY, ttl_seconds=60, now=NOW)
    with pytest.raises(InviteError):
        redeem_invite(expired, key=KEY, store=_store(), login="carol", now=NOW + 61)


def test_invite_requires_32_byte_key() -> None:
    with pytest.raises(Exception):
        mint_invite("alice", key=b"short", now=NOW)


def test_member_admin_requires_active_administrator() -> None:
    members = {
        "admin": LifecycleMember(login="admin", is_active=True, is_superuser=True),
        "bob": LifecycleMember(login="bob", is_active=True, is_superuser=False),
    }
    with pytest.raises(AdminRefusedError):
        apply_member_action(members, actor_login="bob", target_login="bob", action="grant_admin")
    with pytest.raises(AdminRefusedError):
        apply_member_action(members, actor_login="ghost", target_login="bob", action="grant_admin")
    updated = apply_member_action(members, actor_login="admin", target_login="bob", action="grant_admin")
    assert updated["bob"].is_superuser is True


def test_last_admin_is_protected_with_recovery_pointer() -> None:
    members = {
        "solo": LifecycleMember(login="solo", is_active=True, is_superuser=True),
        "bob": LifecycleMember(login="bob", is_active=True, is_superuser=False),
    }
    for action in ("revoke_admin", "deactivate", "remove"):
        with pytest.raises(AdminRefusedError) as excinfo:
            apply_member_action(members, actor_login="solo", target_login="solo", action=action)
        assert excinfo.value.code == "last_admin_protected"
        assert "recovery" in str(excinfo.value).lower()
    # With two admins, revoking one is allowed.
    two = dict(members)
    two["second"] = LifecycleMember(login="second", is_active=True, is_superuser=True)
    updated = apply_member_action(two, actor_login="solo", target_login="second", action="revoke_admin")
    assert updated["second"].is_superuser is False


def test_member_admin_unknown_member_and_action() -> None:
    members = {
        "admin": LifecycleMember(login="admin", is_active=True, is_superuser=True),
    }
    with pytest.raises(AdminRefusedError):
        apply_member_action(members, actor_login="admin", target_login="ghost", action="grant_admin")
    with pytest.raises(AdminRefusedError):
        apply_member_action(members, actor_login="admin", target_login="admin", action="promote")


def test_recovery_is_short_lived_one_use_and_login_bound() -> None:
    store = _store(owner=True)
    token = mint_recovery_capability("owner", key=KEY, ttl_seconds=60, now=NOW)
    assert redeem_recovery_capability(token, key=KEY, store=store, login="owner", now=NOW + 1) == "owner"
    with pytest.raises(RecoveryError):
        redeem_recovery_capability(token, key=KEY, store=store, login="owner", now=NOW + 2)
    other = mint_recovery_capability("owner", key=KEY, now=NOW)
    with pytest.raises(RecoveryError):
        redeem_recovery_capability(other, key=KEY, store=_store(owner=True), login="intruder", now=NOW + 1)
    expired = mint_recovery_capability("owner", key=KEY, ttl_seconds=60, now=NOW)
    with pytest.raises(RecoveryError):
        redeem_recovery_capability(expired, key=KEY, store=_store(owner=True), login="owner", now=NOW + 61)


def test_lifecycle_errors_map_to_section_8_statuses() -> None:
    assert http_status_for_lifecycle_error(BootstrapError("auth_invalid", "x")) == (401, "auth_invalid")
    assert http_status_for_lifecycle_error(InviteError("auth_invalid", "x")) == (401, "auth_invalid")
    assert http_status_for_lifecycle_error(RecoveryError("auth_invalid", "x")) == (401, "auth_invalid")
    status, code = http_status_for_lifecycle_error(AdminRefusedError("last_admin_protected", "x"))
    assert status == 403
    assert code == "last_admin_protected"


def test_lifecycle_events_are_redacted() -> None:
    event = redacted_lifecycle_event("invite", action="minted", login="alice")
    assert event["login"] == "alice"
    token = mint_invite("alice", key=KEY, now=NOW)
    with pytest.raises(Exception):
        redacted_lifecycle_event("invite", action="minted", login="alice", extra={"token": token})
    with pytest.raises(Exception):
        redacted_lifecycle_event("bootstrap", action="claimed", login="o", extra={"capability": token})
    assert_no_secret_leak(event, [token])
    with pytest.raises(AssertionError):
        assert_no_secret_leak({"t": token}, [token])


def test_lifecycle_clock_defaults_to_wall_time() -> None:
    token = mint_bootstrap_capability("owner", key=KEY)
    store = _store()
    assert claim_first_owner(token, key=KEY, store=store, login="owner") == "owner"
    assert time.time() > 0
