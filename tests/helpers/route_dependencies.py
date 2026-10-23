"""Shared test overrides for the one product admission dependency."""

from api_service.auth_providers import get_instance_access


def _resolve_user_dependency_overrides():
    return [get_instance_access]
