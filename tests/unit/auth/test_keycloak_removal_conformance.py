"""Keycloak-removal conformance baseline (MoonLadderStudios/MoonMind#4128).

Pre-cutover hermetic baseline for the final qualification issue. Keycloak is
still live in the production auth path (``api_service/auth_providers.py``,
``moonmind/config/settings.py``, ``docker-compose.yaml``), so these tests pin
the current inventory and the invariants the cutover must preserve — they do
not claim the full browser / multi-instance / OIDC migration matrix, which is
owned by the #4118-4127 feature work and its integrated qualification.

Each test names the follow-up that must update it when the cutover lands.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_auth_provider_inventory_is_explicit() -> None:
    """Pin the accepted AUTH_PROVIDER literals (pre-cutover inventory).

    #4117 inventory boundary: settings, the request dependency, the mounted
    routes, and startup must be changed together. When the cutover adds
    accounts/OIDC/header/restricted-local modes, this test must be extended —
    a new accepted literal without a test owner here is a regression.
    """
    settings_src = _read("moonmind/config/settings.py")
    providers_src = _read("api_service/auth_providers.py")
    main_src = _read("api_service/main.py")

    assert 'AUTH_PROVIDER: str = Field(' in settings_src
    assert '"disabled"' in settings_src
    # Pre-cutover baseline: the setting description still names the Keycloak
    # mode. The cutover must update the description alongside the behavior.
    assert "keycloak" in settings_src

    # The request dependency distinguishes disabled from authenticated modes.
    assert 'settings.oidc.AUTH_PROVIDER != "disabled"' in providers_src
    # The router inventory still carries the Keycloak placeholder branch and
    # the separate default branch. Both must be replaced together, not just
    # the helper above.
    assert 'settings.oidc.AUTH_PROVIDER == "keycloak"' in providers_src
    assert 'settings.oidc.AUTH_PROVIDER == "default"' in providers_src

    # Mounted routes and startup branch on the same literals.
    assert 'settings.oidc.AUTH_PROVIDER != "keycloak"' in main_src
    assert 'settings.oidc.AUTH_PROVIDER == "disabled"' in main_src


def test_settings_code_default_disables_nothing_silently() -> None:
    """The code default must remain an explicit local mode, never silent auth-off."""
    settings_src = _read("moonmind/config/settings.py")

    match = re.search(
        r"AUTH_PROVIDER: str = Field\(\s*\"([^\"]+)\"", settings_src
    )
    assert match is not None, "AUTH_PROVIDER default literal must stay explicit"
    assert match.group(1) == "disabled"

    # OIDC issuer must default to None in code (no hardcoded live IdP); the
    # keycloak hostname default lives only in Compose environment examples.
    assert re.search(
        r"OIDC_ISSUER_URL: Optional\[str\] = Field\(\s*None", settings_src
    ) is not None


def test_disabled_mode_keeps_explicit_default_user_path() -> None:
    """Disabled mode must resolve an explicit default user, not bypass auth."""
    providers_src = _read("api_service/auth_providers.py")

    assert "get_default_user_from_db" in providers_src
    assert "_disabled_auth_fallback_user" in providers_src
    assert "Default user not found" in providers_src
    assert "Invalid DEFAULT_USER_ID" in providers_src


def test_non_disabled_mode_uses_authenticated_user_dependency() -> None:
    """Non-disabled modes must use the authenticated user dependency."""
    providers_src = _read("api_service/auth_providers.py")

    assert "current_active_user" in providers_src
    assert "current_active_user_optional" in providers_src
    # The strict dependency is returned for every non-disabled mode; the
    # optional variant exists only for the worker-token evaluation path.
    assert "return current_active_user" in providers_src
    assert "return current_active_user_optional" in providers_src


def test_worker_auth_keeps_separate_authority() -> None:
    """Worker credential evaluation must stay separate from browser auth."""
    worker_src = _read("api_service/api/routers/worker_auth.py")

    assert "get_current_user_optional" in worker_src
    # Legacy worker tokens are rejected, not silently accepted.
    assert "410" in worker_src
    assert "worker_token_deprecated" in worker_src
    # Missing credentials are denied, not defaulted.
    assert "401" in worker_src
    assert "auth_required" in worker_src


def test_artifact_authorization_error_boundary_exists() -> None:
    """Ownership enforcement must exist independent of the provider string."""
    artifacts_src = _read("moonmind/workflows/temporal/artifacts.py")

    assert "class TemporalArtifactAuthorizationError" in artifacts_src
    assert "cannot read" in artifacts_src
    # The disabled-auth bypass selector must exist as an explicit branch so
    # new authenticated modes do not inherit it by accident.
    assert "disabled" in artifacts_src


def test_keycloak_compose_topology_is_profile_gated() -> None:
    """Pin the pre-cutover topology: Keycloak exists but never starts by default.

    Removal must delete the service and update this test in the same change;
    flipping this assertion to "no keycloak service" before the code cutover
    lands would be a false qualification claim.
    """
    import yaml

    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
    services = compose.get("services", {})

    assert "keycloak" in services, "pre-cutover baseline still ships Keycloak topology"
    profiles = services["keycloak"].get("profiles", [])
    assert "keycloak" in profiles, "Keycloak must stay opt-in via its profile"
    # The default API image must not depend on Keycloak being up.
    api_depends = (services.get("api", {}).get("depends_on", {}) or {})
    assert "keycloak" not in api_depends


def test_auth_error_paths_scrub_secrets() -> None:
    """Auth/startup failure paths must redact secrets before logging."""
    main_src = _read("api_service/main.py")

    assert "SecretRedactor" in main_src
    # The default-user startup path scrubs its error before logging.
    assert "redacted_error" in main_src

    providers_src = _read("api_service/auth_providers.py")
    for line in providers_src.splitlines():
        if "logger.warning" in line or "logger.error" in line or "logger.exception" in line:
            assert "OIDC_CLIENT_SECRET" not in line
            assert "JWT_SECRET" not in line


def test_no_hardcoded_keycloak_credentials_in_api_sources() -> None:
    """Narrow no-leak guard: API sources must not embed Keycloak secrets."""
    for relative in (
        "api_service/auth.py",
        "api_service/auth_providers.py",
        "api_service/main.py",
        "api_service/api/routers/worker_auth.py",
    ):
        src = _read(relative)
        assert "KEYCLOAK_ADMIN_PASSWORD" not in src, relative
        assert "KC_ADMIN_PW" not in src, relative
        assert "changeme" not in src, relative
