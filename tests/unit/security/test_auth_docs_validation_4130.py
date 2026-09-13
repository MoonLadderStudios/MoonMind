"""Documentation validation checks for #4130 (parent #4116).

Records the RW-5/AC-5/AC-6 validation the verifier carried as missing
evidence: relative-link resolution over the reconciled docs,
configuration-example validation of the ``AUTH_PROVIDER`` selector from
``.env-template`` against the real implementation, and the active-claim
zero-state (no live Keycloak setup claims or realm URLs outside
classified residuals).

Hermetic and offline: only repo-relative links are checked (no network);
external URLs are allow-listed as classified-intentional or reported as
informational, never fetched.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

CANONICAL_DOC = REPO_ROOT / "docs/Security/AuthenticationContracts.md"
ADAPTER_DOC = REPO_ROOT / "docs/Security/OmnigentAuthAdapterContract.md"
LEDGER = REPO_ROOT / "docs/tmp/KeycloakRemovalStatus-4130.md"
RESIDUAL_MANIFEST = REPO_ROOT / "docs/tmp/KeycloakRemovalResidual-4129.md"
README = REPO_ROOT / "README.md"
ENV_TEMPLATE = REPO_ROOT / ".env-template"
MAIN_CLI = REPO_ROOT / "moonmind/cli.py"
API_MAIN = REPO_ROOT / "api_service/main.py"

RECONCILED_DOCS = [
    REPO_ROOT / "docs/Security/AuthenticationContracts.md",
    REPO_ROOT / "docs/ExternalAgents/ModelContextProtocol.md",
    REPO_ROOT / "docs/ManagedAgents/DockerBackendService.md",
    REPO_ROOT / "docs/Temporal/WorkflowArtifactSystemDesign.md",
    REPO_ROOT / "docs/Omnigent/CombinedStackValidationAndRollback.md",
    REPO_ROOT / "README.md",
]

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_REALM_URL_RES = [
    re.compile(r"keycloak:8080", re.IGNORECASE),
    re.compile(r"realms/moonmind", re.IGNORECASE),
    re.compile(r"http://keycloak", re.IGNORECASE),
]
_LIVE_SETUP_CLAIMS = [
    "Keycloak setup",
    "keycloak setup",
    "Start Keycloak",
    "start keycloak",
    "KC_DB_PW",
    "realm-export.json",
]


def _relative_link_targets(doc: Path) -> list[str]:
    targets: list[str] = []
    for match in _LINK_RE.finditer(doc.read_text(encoding="utf-8")):
        target = match.group(1).strip()
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = target.split("#")[0].strip()
        if target:
            targets.append(target)
    return targets


def test_reconciled_docs_relative_links_resolve() -> None:
    missing: list[str] = []
    checked = 0
    for doc in RECONCILED_DOCS:
        assert doc.exists(), f"reconciled doc missing: {doc}"
        for target in _relative_link_targets(doc):
            checked += 1
            resolved = (doc.parent / target).resolve()
            try:
                resolved.relative_to(REPO_ROOT.resolve())
            except ValueError:
                missing.append(f"{doc.name}: {target} escapes the repo")
                continue
            if not resolved.exists():
                missing.append(f"{doc.name}: {target}")
    assert checked > 0, "expected relative links in the reconciled docs"
    assert missing == [], f"dead relative links: {missing[:10]}"


def test_canonical_doc_tmp_backlinks_resolve() -> None:
    for name in ("KeycloakRemovalStatus-4130.md", "KeycloakRemovalResidual-4129.md", "KeycloakRemovalPlan.md"):
        assert (REPO_ROOT / "docs/tmp" / name).exists(), f"tmp backlink target missing: {name}"
    assert LEDGER.exists()
    assert RESIDUAL_MANIFEST.exists()


def test_env_template_selector_examples_match_implementation() -> None:
    from moonmind.config.settings import OIDCSettings
    from moonmind.security.auth_modes_4120 import (
        classify_deployment,
        resolve_production_mode,
    )
    from moonmind.security.omnigent_auth_qualification import validate_mode_selector

    text = ENV_TEMPLATE.read_text(encoding="utf-8")
    # The template documents the fresh-install omitted-selector path and the
    # populated-database migration decision; both must behave as documented.
    assert 'AUTH_PROVIDER=""' in text
    assert "MOONMIND_AUTH_MIGRATION_DECISION" in text
    assert resolve_production_mode(raw_selector="", explicit=False, has_users=False) == "accounts"
    # Every documented supported mode validates through the real owner.
    for mode in ("accounts", "oidc", "header", "disabled"):
        assert mode in text
        assert validate_mode_selector(mode) == mode
    # Every retired selector named in the template is rejected by settings.
    for retired in OIDCSettings.RETIRED_AUTH_PROVIDERS:
        assert retired in text
        with _raises():
            OIDCSettings.validate_auth_provider(retired)
    # Documented OIDC/header example values are well-formed inputs.
    assert "MOONMIND_OIDC_ISSUER" in text
    assert "MOONMIND_TRUSTED_PROXIES" in text
    # Fresh installs require protected setup, never a silent admin grant.
    fresh = classify_deployment(raw_selector="", explicit=False, has_users=False)
    assert fresh.production_mode == "accounts"
    assert fresh.setup_required is True
    assert fresh.migration_required is False
    upgraded = classify_deployment(raw_selector="", explicit=False, has_users=True)
    assert upgraded.migration_required is True


class _raises:
    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return exc_type is not None


def test_no_active_realm_urls_outside_classified_residuals() -> None:
    """No active file may contain a live Keycloak realm URL.

    Classified homes for historical/negative-test/migration mentions are the
    tmp ledger + residual manifest + removal plan + inventory, the settings
    retired-selector rejection, negative tests, and historical migrations.
    """
    offenders: list[str] = []
    roots = [
        REPO_ROOT / "docs/Security",
        REPO_ROOT / "docs/ExternalAgents",
        REPO_ROOT / "docs/ManagedAgents",
        REPO_ROOT / "docs/Temporal",
        REPO_ROOT / "docs/Omnigent",
        REPO_ROOT / "examples",
        REPO_ROOT / "frontend/src/generated",
    ]
    extra_files = [README, MAIN_CLI, API_MAIN, REPO_ROOT / "docker-compose.yaml"]
    candidates: list[Path] = []
    for root in roots:
        if root.is_file():
            candidates.append(root)
        elif root.is_dir():
            candidates.extend(p for p in root.rglob("*") if p.is_file())
    candidates.extend(extra_files)
    for path in sorted(set(candidates)):
        try:
            content = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            # Placeholder hosts (RFC 2606 `.invalid`) are documentation
            # examples, never live realm URLs (e.g. the canonical §12.5
            # `https://idp.example.invalid/realms/moonmind` OIDC sample).
            if "example.invalid" in line:
                continue
            for pattern in _REALM_URL_RES:
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {pattern.pattern}")
    assert offenders == [], f"active realm URLs remain: {offenders[:10]}"


def test_no_active_keycloak_setup_claims() -> None:
    """Active onboarding/operator/client guidance must not claim retired setup works."""
    offenders: list[str] = []
    for path in [README, MAIN_CLI, API_MAIN, *RECONCILED_DOCS]:
        content = path.read_text(encoding="utf-8")
        for claim in _LIVE_SETUP_CLAIMS:
            if claim in content:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {claim!r}")
    examples_hits: list[str] = []
    examples_dir = REPO_ROOT / "examples"
    for child in sorted(examples_dir.rglob("*")):
        if not child.is_file():
            continue
        try:
            content = child.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        if "keycloak" in content.lower():
            examples_hits.append(str(child.relative_to(REPO_ROOT)))
    assert offenders == [], f"active Keycloak setup claims remain: {offenders[:10]}"
    assert examples_hits == [], f"examples mention keycloak: {examples_hits[:10]}"


def test_residual_manifest_classifies_remaining_mentions() -> None:
    text = RESIDUAL_MANIFEST.read_text(encoding="utf-8")
    assert "Justified residuals" in text
    assert "RETIRED_AUTH_PROVIDERS" in text
    assert "#4125" in text  # partly-superseded row annotation for #4124/#4125
