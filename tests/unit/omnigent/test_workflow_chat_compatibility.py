"""Pinned native Omnigent transport compatibility contract for issue #3635."""

from __future__ import annotations

import json
from pathlib import Path

from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT
from moonmind.omnigent.workflow_chat_facade import (
    FACADE_COMPATIBILITY_PROFILE,
    FACADE_OPERATIONS,
)


_ROOT = Path(__file__).resolve().parents[3]
_PINNED_HTTP_SURFACE = {
    "/v1/sessions/{session_id}/child_sessions": {"get"},
    "/v1/sessions/{session_id}/resources": {"get"},
    "/v1/sessions/{session_id}/resources/environments": {"get"},
    "/v1/sessions/{session_id}/resources/environments/{environment_id}": {"get"},
    "/v1/sessions/{session_id}/resources/environments/{environment_id}/changes": {
        "get"
    },
    "/v1/sessions/{session_id}/resources/environments/{environment_id}/filesystem": {
        "get"
    },
    (
        "/v1/sessions/{session_id}/resources/environments/{environment_id}"
        "/filesystem/{relative_path}"
    ): {
        "delete",
        "get",
        "patch",
        "put",
    },
    "/v1/sessions/{session_id}/resources/environments/{environment_id}/search": {"get"},
    "/v1/sessions/{session_id}/resources/environments/{environment_id}/shell": {"post"},
    "/v1/sessions/{session_id}/resources/files": {"get", "post"},
    "/v1/sessions/{session_id}/resources/files/{file_id}": {"delete", "get"},
    "/v1/sessions/{session_id}/resources/files/{file_id}/content": {"get"},
    "/v1/sessions/{session_id}/resources/files:copy": {"post"},
    "/v1/sessions/{session_id}/resources/terminals": {"get", "post"},
    "/v1/sessions/{session_id}/resources/terminals/{terminal_id}": {"delete", "get"},
}


def test_compatibility_profile_is_tied_to_the_single_upstream_pin() -> None:
    assert FACADE_COMPATIBILITY_PROFILE.endswith(PINNED_OMNIGENT_COMMIT[:12])


def test_pinned_openapi_contains_the_reviewed_native_workspace_surface() -> None:
    document = json.loads((_ROOT / "omnigent" / "openapi.json").read_text())
    paths = document["paths"]
    for route, methods in _PINNED_HTTP_SURFACE.items():
        assert route in paths
        assert methods <= paths[route].keys()


def test_compatibility_map_covers_each_transport_family_and_fails_closed() -> None:
    names = {operation.name for operation in FACADE_OPERATIONS}
    assert {
        "session_items",
        "child_sessions",
        "workspace_files",
        "workspace_write",
        "workspace_edit",
        "workspace_delete",
        "workspace_search",
        "environment_shell",
        "upload_session_file",
        "delete_session_file",
        "terminals",
        "create_terminal",
        "terminal_status",
        "close_terminal",
        "attach_terminal",
        "session_updates",
        "liveness",
    } <= names
    assert all(
        operation.pattern.pattern.startswith("^") for operation in FACADE_OPERATIONS
    )
    assert all(
        operation.pattern.pattern.endswith("$") for operation in FACADE_OPERATIONS
    )
