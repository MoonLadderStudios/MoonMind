"""No-.env startup through rendered Compose and settings/admission wiring.

Source issue: MoonLadderStudios/MoonMind#3941 (acceptance REQ-02).

The supported default path starts with no mandatory overrides: Compose must
render with an empty environment, the typed settings catalog must resolve
documented effective values from that state, the admission snapshot must bind,
and captured diagnostics must exclude secret values.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import yaml

from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.config.effective_policy_snapshot import (
    bind_effective_policy_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = [
    REPO_ROOT / "docker-compose.yaml",
    REPO_ROOT / "docker-compose.test.yaml",
    REPO_ROOT / "docker-compose.development.yaml",
]

_INTERPOLATION = re.compile(
    r"\$(?:\$(?P<escaped>\$)|\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<op>:?[-+?])?(?P<word>[^}]*)\}|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)


def _resolve_compose_text(text: str, env: dict[str, str]) -> str:
    """Resolve Compose-style interpolation against ``env`` (``$$`` escaped)."""

    def _replace(match: re.Match[str]) -> str:
        if match.group("escaped"):
            return "$"
        name = match.group("braced") or match.group("bare")
        op = match.group("op") or ""
        word = match.group("word") or ""
        if op in ("?", ":?"):
            if name not in env or (op == ":?" and not env[name]):
                raise ValueError(f"required variable {name} is unset")
            return env[name]
        if op in ("-", ":-"):
            if name not in env or (op == ":-" and not env[name]):
                return word
            return env[name]
        if op in ("+", ":+"):
            if name not in env or (op == ":+" and not env[name]):
                return ""
            return word
        return env.get(name, "")

    return _INTERPOLATION.sub(_replace, text)


def _stub_settings() -> SimpleNamespace:
    return SimpleNamespace(
        workflow=SimpleNamespace(
            default_runtime="codex_cli",
            default_publish_mode="pr",
            moonspec_environment_blocked_publish_action="fail",
            skill_policy_mode="permissive",
            skills_canary_percent=100,
            default_provider_profile_ref=None,
        )
    )


def test_compose_has_no_required_variables():
    violations: list[str] = []
    for path in COMPOSE_FILES:
        for match in _INTERPOLATION.finditer(
            path.read_text(encoding="utf-8")
        ):
            if match.group("escaped"):
                continue
            op = match.group("op") or ""
            if op in ("?", ":?"):
                violations.append(
                    f"{path.name}: required interpolation {match.group(0)}"
                )
    assert not violations, (
        "first-run startup must not require .env values: " + "; ".join(violations)
    )


def test_compose_renders_with_an_empty_environment():
    for path in COMPOSE_FILES:
        text = path.read_text(encoding="utf-8")
        rendered = _resolve_compose_text(text, {})
        parsed = yaml.safe_load(rendered)
        assert isinstance(parsed, dict), path.name
        assert "services" in parsed, path.name


def test_settings_and_admission_resolve_with_an_empty_environment():
    service = SettingsCatalogService(settings=_stub_settings(), env={})
    response = service.effective_values(scope="workspace")

    # Nothing is invented from an empty environment: secrets stay unresolved
    # (None) instead of falling back to another credential.
    assert response.values["integrations.github.token_ref"].value is None
    assert (
        response.values["workflow.default_provider_profile_ref"].value is None
    )

    snapshot = bind_effective_policy_snapshot(response)
    assert len(snapshot.entries) == len(
        {entry.key for entry in snapshot.entries}
    )


def test_documented_override_changes_effective_state_without_secrets():
    service = SettingsCatalogService(
        settings=_stub_settings(),
        env={
            "WORKFLOW_SKILLS_CANARY_PERCENT": "25",
            "MOONMIND_GITHUB_TOKEN_REF": "db://first-run-github-token",
        },
    )
    response = service.effective_values(scope="workspace")
    assert response.values["skills.canary_percent"].value == 25
    assert response.values["skills.canary_percent"].source == "environment"

    snapshot = bind_effective_policy_snapshot(response)
    diagnostics = snapshot.to_diagnostic_dict()
    serialized = json.dumps(diagnostics)
    assert "first-run-github-token" not in serialized
    assert (
        diagnostics["entries"]["skills.canary_percent"]["value"] == 25
    )
