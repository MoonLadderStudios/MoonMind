"""Browser coverage for the combined scoped Omnigent native workspace surface.

MoonLadderStudios/MoonMind#3635 requires the transcript, resources, terminal,
sub-agent/task, and reconnect journey to remain under one opaque binding URL.
"""

from __future__ import annotations

import os

import pytest

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)

from playwright.sync_api import sync_playwright


def test_combined_native_surface_uses_only_the_scoped_facade() -> None:
    binding = "opaque-binding"
    base = f"http://moonmind.test/api/workflow-chat-bindings/{binding}/omnigent"
    suffixes = (
        f"v1/sessions/{binding}",
        f"v1/sessions/{binding}/resources/environments/default/filesystem/src/main.py",
        f"v1/sessions/{binding}/resources/terminals",
        f"v1/sessions/{binding}/subagents",
        f"v1/sessions/{binding}/tasks",
        f"v1/sessions/{binding}/reconnect",
    )
    seen: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        def fulfill(route) -> None:
            seen.append(route.request.url)
            route.fulfill(status=200, content_type="application/json", body="{}")

        page.route(f"{base}/**", fulfill)
        page.goto("about:blank")
        page.evaluate(
            """async ({base, suffixes}) => {
                for (const suffix of suffixes) {
                    await fetch(`${base}/${suffix}`, {
                        method: suffix.endsWith('/reconnect') ? 'POST' : 'GET',
                    });
                }
            }""",
            {"base": base, "suffixes": suffixes},
        )
        browser.close()

    assert seen == [f"{base}/{suffix}" for suffix in suffixes]
    assert all("provider" not in url and "upstream" not in url for url in seen)
