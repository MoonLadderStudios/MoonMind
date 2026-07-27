"""Permission boundary evidence for operator-owned Omnigent policies."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api_service.api.routers.omnigent_policies import (
    _can_view_policy,
    _require_policy_management,
)


def _policy(*, owner=None, visibility="private"):
    return SimpleNamespace(owner_user_id=owner, visibility=visibility)


def _user(user_id=None, *, superuser=False):
    return SimpleNamespace(id=user_id, is_superuser=superuser)


def test_private_policy_is_visible_only_to_owner_or_superuser() -> None:
    owner = uuid4()
    assert _can_view_policy(_policy(owner=owner), _user(owner))
    assert not _can_view_policy(_policy(owner=owner), _user(uuid4()))
    assert _can_view_policy(_policy(owner=owner), _user(uuid4(), superuser=True))


def test_deployment_policy_is_readable_but_not_mutable_by_non_owner() -> None:
    owner = uuid4()
    other = _user(uuid4())
    policy = _policy(owner=owner, visibility="deployment")
    assert _can_view_policy(policy, other)
    with pytest.raises(HTTPException) as exc:
        _require_policy_management(policy, other)
    assert exc.value.status_code == 403


def test_unowned_bootstrap_policy_requires_superuser_for_management() -> None:
    policy = _policy(owner=None, visibility="deployment")
    with pytest.raises(HTTPException):
        _require_policy_management(policy, _user(uuid4()))
    _require_policy_management(policy, _user(uuid4(), superuser=True))
