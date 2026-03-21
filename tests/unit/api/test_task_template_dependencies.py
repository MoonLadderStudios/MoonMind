"""Unit tests for task template dependency helpers."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api_service.api.dependencies import resolve_template_scope_for_user


def _user(*, is_superuser: bool = False):
    return SimpleNamespace(id=uuid4(), is_superuser=is_superuser)


def test_resolve_personal_scope_defaults_scope_ref_to_user() -> None:
    user = _user()
    scope, scope_ref = resolve_template_scope_for_user(
        user=user,
        scope="personal",
        scope_ref=None,
        write=False,
    )

    assert scope == "personal"
    assert scope_ref == str(user.id)


def test_resolve_personal_scope_rejects_other_user_for_non_admin() -> None:
    user = _user()
    with pytest.raises(HTTPException) as exc:
        resolve_template_scope_for_user(
            user=user,
            scope="personal",
            scope_ref=str(uuid4()),
            write=False,
        )

    assert exc.value.status_code == 403


def test_resolve_global_write_requires_admin() -> None:
    user = _user(is_superuser=False)
    with pytest.raises(HTTPException) as exc:
        resolve_template_scope_for_user(
            user=user,
            scope="global",
            scope_ref=None,
            write=True,
        )

    assert exc.value.status_code == 403


def test_resolve_global_write_allows_admin() -> None:
    admin = _user(is_superuser=True)
    scope, scope_ref = resolve_template_scope_for_user(
        user=admin,
        scope="global",
        scope_ref=None,
        write=True,
    )

    assert scope == "global"
    assert scope_ref is None


def test_resolve_team_scope_rejects_non_owner_reads_for_non_admin() -> None:
    user = _user()
    with pytest.raises(HTTPException) as exc:
        resolve_template_scope_for_user(
            user=user,
            scope="team",
            scope_ref=str(uuid4()),
            write=False,
        )

    assert exc.value.status_code == 403


def test_resolve_team_scope_allows_admin_reads() -> None:
    admin = _user(is_superuser=True)
    scope, scope_ref = resolve_template_scope_for_user(
        user=admin,
        scope="team",
        scope_ref=str(uuid4()),
        write=False,
    )

    assert scope == "team"
    assert scope_ref is not None
