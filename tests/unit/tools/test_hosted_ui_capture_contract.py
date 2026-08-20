"""Pin the hosted-UI capture's bootstrap assertions to the real application.

Source issue: MoonLadderStudios/MoonMind#3710.

The Tier-1 exact-artifact gate claims the compiled native UI baked into the
deployable image *consumes its hosted bootstrap*. The capture originally proved
that by counting any same-origin request, which the top-level navigation always
satisfies: a compiled UI that never loaded, or never read its boot payload,
still reported success and hid exactly the bootstrap regression the gate exists
to catch.

The capture now asserts concrete, observable facts. This hermetic test pins each
of them to the real served document and static mount, so renaming the boot
script element, the app root, or the compiled-asset mount fails a unit test
instead of silently weakening — or falsely failing — the Tier-1 gate.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE = REPO_ROOT / "tools/run_omnigent_browser_journey.mjs"
TEMPLATE = REPO_ROOT / "api_service/templates/react_dashboard.html"


def _capture_source() -> str:
    return CAPTURE.read_text(encoding="utf-8")


def _bundle_prefix() -> str:
    match = re.search(
        r'HOSTED_BUNDLE_PATH_PREFIX\s*=\s*"([^"]+)"', _capture_source()
    )
    assert match, "the capture must declare the compiled-bundle path prefix"
    return match.group(1)


def test_bundle_prefix_matches_the_image_static_mount() -> None:
    main_source = (REPO_ROOT / "api_service/main.py").read_text(encoding="utf-8")

    prefix = _bundle_prefix()

    # api_service.main mounts the compiled Vite output here; a request under it
    # proves the browser fetched the UI baked into the deployable image.
    assert '"/static/workflow_console/dist"' in main_source
    assert prefix == "/static/workflow_console/dist/"


def test_boot_payload_and_app_root_selectors_exist_in_the_served_document() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    source = _capture_source()

    assert 'id="moonmind-ui-boot"' in template
    assert 'id="dashboard-app-root"' in template
    assert "#moonmind-ui-boot" in source
    assert "#dashboard-app-root" in source


def test_capture_requires_all_three_bootstrap_facts() -> None:
    """Bundle fetched, boot payload parsed, and application rendered."""
    source = _capture_source()

    match = re.search(
        r"const consumedHostedBootstrap =\s*(.+?);", source, re.DOTALL
    )
    assert match, "the capture must derive bootstrap consumption explicitly"
    expression = match.group(1)

    assert "bundleRequested" in expression
    assert "bootPayloadParsed" in expression
    assert "appRendered" in expression
    # The superseded predicate counted any non-/v1/ same-origin request, which
    # the navigation document itself always satisfies.
    assert "requests.some" not in expression


def test_capture_accepts_an_explicit_hosted_url() -> None:
    """The probe runs on the CI host against the container's hosted origin."""
    source = _capture_source()

    assert '--hosted-url' in source
    assert "runHostedNetworkCapture(cliArgs)" in source
