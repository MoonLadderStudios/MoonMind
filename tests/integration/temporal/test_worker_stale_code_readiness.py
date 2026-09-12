"""Compose-backed stale worker code readiness test (MoonLadderStudios/MoonMind#4224).

Runs in the ``moonmind-test`` Compose project: it starts a worker healthcheck
server against a bind-mounted style module directory, modifies a module, and
asserts the readiness endpoint reports ``stale_code`` for that worker with
both identities; re-recording the identity (a restart) clears it.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest

from moonmind.workflows.temporal import worker_code_identity as wci


def _fetch_readyz_no_proxy(port: int, timeout: float = 5.0) -> tuple[int | None, dict]:
    """Fetch /readyz bypassing proxy env (localhost must never go via squid)."""

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        raw = opener.open(f"http://127.0.0.1:{port}/readyz", timeout=timeout).read()
        return None, json.loads(raw)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


from moonmind.workflows.temporal.worker_code_identity import (
    resolve_worker_code_identity,
)
from moonmind.workflows.temporal.worker_healthcheck import (
    WorkerHealthState,
    start_healthcheck_server,
)


@pytest.fixture(autouse=True)
def _isolated_code_identity(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.delenv("MOONMIND_BUILD_SHA", raising=False)
    monkeypatch.delenv("MOONMIND_IMAGE_DIGEST", raising=False)
    monkeypatch.setenv("MOONMIND_CODE_PACKAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "workflow")
    wci._STARTUP_IDENTITY = None
    yield
    wci._STARTUP_IDENTITY = None


@pytest.mark.integration_ci
@pytest.mark.asyncio
async def test_bind_mounted_module_change_reports_stale_code_until_restart(
    tmp_path,
) -> None:
    """Modifying a bind-mounted module surfaces stale_code; restart clears it."""

    module = tmp_path / "bind_mounted_worker_module.py"
    module.write_text("HANDLER_VERSION = 1\n", encoding="utf-8")

    # Worker boots and records its startup code identity.
    startup = resolve_worker_code_identity(package_root=tmp_path)
    assert startup.digest is not None
    state = WorkerHealthState(
        temporal_connected=True,
        workers_constructed=True,
        pollers_started=True,
        code_revision=startup.revision,
        code_digest=startup.digest,
        code_identity_source=startup.source,
    )
    server = await start_healthcheck_server(state)
    assert server is not None
    port = server.sockets[0].getsockname()[1]
    loop = asyncio.get_running_loop()

    def _get_readyz() -> tuple[int | None, dict]:
        return _fetch_readyz_no_proxy(port)

    try:
        code, body = await loop.run_in_executor(None, _get_readyz)
        assert code is None, body
        assert body["ready"] is True
        assert body["codeIdentityStatus"] == "healthy"

        # Host rewrites a bind-mounted module without restarting the worker.
        # The bounded background refresh detects the rewrite without making
        # any HTTP request perform the source scan.
        module.write_text("HANDLER_VERSION = 2\n", encoding="utf-8")

        async with asyncio.timeout(7):
            while True:
                code, body = await loop.run_in_executor(None, _get_readyz)
                if body.get("codeIdentityStatus") == "stale":
                    break
                await asyncio.sleep(0.05)
        assert code == 503, body
        assert body["ready"] is False  # stale code must not accept new work
        assert state.pollers_started is True
        assert body["codeIdentityStatus"] == "stale"
        assert body["reasonCode"] == "stale_code"
        assert body["staleCode"]["worker"] == "workflow"
        assert (
            body["staleCode"]["startupRevision"]
            == (startup.revision or "unknown")
        )
        assert (
            body["staleCode"]["currentRevision"]
            == (startup.revision or "unknown")
        )

        # Restarting the worker re-records the identity and clears stale_code.
        restarted = resolve_worker_code_identity(package_root=tmp_path)
        state.code_revision = restarted.revision
        state.code_digest = restarted.digest
        state.code_identity_source = restarted.source
        code, body = await loop.run_in_executor(None, _get_readyz)
        assert code is None, body
        assert body["codeIdentityStatus"] == "healthy"
        assert body.get("reasonCode") != "stale_code"
    finally:
        server.close()
        await server.wait_closed()
