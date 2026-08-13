from __future__ import annotations

import httpx
import pytest

from api_service.services import omnigent_native_ui_build
from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT
from moonmind.omnigent.native_ui import (
    NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION,
    NATIVE_UI_ROUTE_TRANSPORT_MANIFEST_DIGEST,
    SUPPORTED_NATIVE_UI_BUILD_IDENTITIES,
)


@pytest.mark.asyncio
async def test_verifier_reads_actual_manifest_and_caches_objective_result(
    monkeypatch,
) -> None:
    calls: list[str] = []
    server_build_id, ui_build_id = SUPPORTED_NATIVE_UI_BUILD_IDENTITIES[
        PINNED_OMNIGENT_COMMIT
    ]
    manifest = {
        "sourceCommit": PINNED_OMNIGENT_COMMIT,
        "serverBuildId": server_build_id,
        "uiBuildId": ui_build_id,
        "hostedBootstrapContractVersion": NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION,
        "routeTransportManifestDigest": NATIVE_UI_ROUTE_TRANSPORT_MANIFEST_DIGEST,
        "compiledBundleConformant": True,
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str, *, headers):
            calls.append(url)
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json=manifest,
            )

    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.internal")
    monkeypatch.setenv("OMNIGENT_NATIVE_UI_VERSION", PINNED_OMNIGENT_COMMIT)
    monkeypatch.setattr(
        omnigent_native_ui_build.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(omnigent_native_ui_build, "_cached_verification", None)

    first = await omnigent_native_ui_build.verify_deployed_native_ui(enabled=True)
    second = await omnigent_native_ui_build.verify_deployed_native_ui(enabled=True)

    assert first.ready is True
    assert first.server_build_id == server_build_id
    assert second == first
    assert calls == ["https://omnigent.internal/api/hosted-build-manifest"]
