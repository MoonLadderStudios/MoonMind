"""Server/frontend dashboard destination registry agreement.

MoonLadderStudios/MoonMind#3822 (parent MoonLadderStudios/MoonMind#3815).

The dashboard resolves destination metadata from the server at runtime and
falls back to the checked-in client registry, so the two must declare the same
destinations, in the same order, with the same Configuration group membership.
``matchesDashboardDestinationRegistry`` only raises a version-skew alert in the
browser; this test fails the build when either side drifts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from api_service.api.routers.workflow_console import DASHBOARD_DESTINATIONS

REPO_ROOT = Path(__file__).resolve().parents[4]
DASHBOARD_ROUTES_TS = REPO_ROOT / "frontend" / "src" / "lib" / "dashboardRoutes.ts"

_ARRAY_START = "export const DASHBOARD_DESTINATIONS: readonly DashboardDestination[] = ["
_GROUPS_START = (
    "export const DASHBOARD_DESTINATION_GROUPS: readonly DashboardDestinationGroup[] = ["
)


def _array_body(source: str, start_marker: str) -> str:
    start = source.index(start_marker) + len(start_marker)
    depth = 1
    for index in range(start, len(source)):
        character = source[index]
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise AssertionError(f"Unterminated array for marker {start_marker!r}")


def _object_literals(body: str) -> list[str]:
    literals: list[str] = []
    depth = 0
    start = 0
    for index, character in enumerate(body):
        if character == "{":
            if depth == 0:
                start = index + 1
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                literals.append(body[start:index])
    return literals


def _scalar(literal: str, key: str) -> Any:
    match = re.search(
        rf"\b{key}:\s*(?:'([^']*)'|\"([^\"]*)\"|(true|false))",
        literal,
    )
    if match is None:
        return None
    if match.group(3) is not None:
        return match.group(3) == "true"
    return match.group(1) if match.group(1) is not None else match.group(2)


def _string_list(literal: str, key: str) -> list[str] | None:
    match = re.search(rf"\b{key}:\s*\[([^\]]*)\]", literal)
    if match is None:
        return None
    return [
        single or double
        for single, double in re.findall(r"'([^']*)'|\"([^\"]*)\"", match.group(1))
    ]


@pytest.fixture(scope="module")
def dashboard_routes_source() -> str:
    assert DASHBOARD_ROUTES_TS.is_file(), f"missing {DASHBOARD_ROUTES_TS}"
    return DASHBOARD_ROUTES_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def frontend_destinations(dashboard_routes_source: str) -> list[dict[str, Any]]:
    literals = _object_literals(_array_body(dashboard_routes_source, _ARRAY_START))
    # A parser that silently matched nothing would make this test vacuous.
    assert literals, "failed to parse the frontend DASHBOARD_DESTINATIONS registry"
    parsed: list[dict[str, Any]] = []
    for literal in literals:
        parsed.append(
            {
                "key": _scalar(literal, "key"),
                "label": _scalar(literal, "label"),
                "iconKey": _scalar(literal, "iconKey"),
                "canonicalPath": _scalar(literal, "canonicalPath"),
                "pathPatterns": _string_list(literal, "pathPatterns"),
                "navigationGroup": _scalar(literal, "navigationGroup"),
                "pageClassification": _scalar(literal, "pageClassification"),
                "capabilityKey": _scalar(literal, "capabilityKey"),
                "endpointKey": _scalar(literal, "endpointKey"),
                "displayMode": _scalar(literal, "displayMode"),
                "menuGroupKey": _scalar(literal, "menuGroupKey"),
            }
        )
    return parsed


def test_frontend_and_server_destination_registries_agree_exactly(
    frontend_destinations: list[dict[str, Any]],
) -> None:
    server = [destination.to_ui_info() for destination in DASHBOARD_DESTINATIONS]
    expected = [
        {
            "key": item["key"],
            "label": item["label"],
            "iconKey": item["iconKey"],
            "canonicalPath": item["canonicalPath"],
            "pathPatterns": item["pathPatterns"],
            "navigationGroup": item["navigationGroup"],
            "pageClassification": item["pageClassification"],
            "capabilityKey": item["capabilityKey"],
            **(
                {"endpointKey": item["endpointKey"]}
                if item["endpointKey"] is not None
                else {}
            ),
            **(
                {"displayMode": item["displayMode"]}
                if item["displayMode"] is not None
                else {}
            ),
            **(
                {"menuGroupKey": item["menuGroupKey"]}
                if item["menuGroupKey"] is not None
                else {}
            ),
        }
        for item in frontend_destinations
    ]
    # Order matters: the browser compares the two registries index by index.
    assert json.dumps(server, indent=2) == json.dumps(expected, indent=2)


def test_configuration_group_membership_matches_the_three_canonical_destinations(
    dashboard_routes_source: str,
) -> None:
    group_literals = _object_literals(
        _array_body(dashboard_routes_source, _GROUPS_START)
    )
    assert len(group_literals) == 1, "Settings exposes exactly one Configuration group"
    group = group_literals[0]
    assert _scalar(group, "key") == "configuration"
    assert _scalar(group, "label") == "Configuration"
    assert _scalar(group, "triggerLabel") == "Settings"
    assert _scalar(group, "triggerIconKey") == "settings"
    assert _string_list(group, "destinationKeys") == [
        "settings-providers-secrets",
        "settings-user-workspace",
        "settings-operations",
    ]

    server_configuration = [
        destination.key
        for destination in DASHBOARD_DESTINATIONS
        if destination.menu_group_key == "configuration"
    ]
    assert server_configuration == [
        "settings-providers-secrets",
        "settings-user-workspace",
        "settings-operations",
    ]
    for destination in DASHBOARD_DESTINATIONS:
        if destination.menu_group_key != "configuration":
            continue
        # Each Configuration child owns its own canonical /settings pathname.
        assert destination.canonical_path.startswith("/settings/")
        assert destination.path_patterns == (destination.canonical_path,)
