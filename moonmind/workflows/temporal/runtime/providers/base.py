"""OAuth provider specification contract."""

from __future__ import annotations

from typing import TypedDict


class OAuthProviderSpec(TypedDict):
    """Per-runtime OAuth session contract.

    Defines everything the session orchestrator needs to provision
    and verify an auth session for a given CLI runtime.
    """

    runtime_id: str
    auth_mode: str
    session_transport: str
    default_volume_name: str
    default_mount_path: str
    bootstrap_command: list[str]
    success_check: str
    account_label_prefix: str
