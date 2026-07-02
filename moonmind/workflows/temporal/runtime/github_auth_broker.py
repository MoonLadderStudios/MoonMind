"""Broker GitHub credentials to local helper scripts without writing PATs to disk."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BROKER_SOCKET_TIMEOUT_SECONDS = 5.0
_BROKER_SOCKET_PATH_MAX_BYTES = 100
_SHARED_WORKSPACE_ROOT = Path("/work/agent_jobs")
_SHARED_SOCKET_DIRNAME = ".moonmind-gh"
_LEGACY_SHARED_SOCKET_DIRNAME = "mm-gh"


def build_github_socket_path(
    *,
    run_id: str,
    support_root: str | None,
    socket_root: str | None = None,
) -> str:
    """Build a short broker socket path visible to managed runtime containers.

    Managed-session agent containers share ``/work/agent_jobs`` with the worker
    process, but they do not share the worker's ``/tmp``. Prefer a compact
    workspace-volume path when the support root lives under that volume, then
    fall back to ``/tmp/mm-gh`` for local/direct subprocess runs.
    """

    material = run_id
    support_path: Path | None = None
    if support_root:
        support_path = Path(support_root).resolve()
        material = f"{support_path}::{run_id}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    if socket_root:
        return str(Path(socket_root) / digest / "github.sock")

    candidate_roots: list[Path] = []
    if support_path is not None:
        workdir = os.environ.get("MOONMIND_WORKDIR")
        if workdir:
            candidate_roots.append(Path(workdir).resolve() / _SHARED_SOCKET_DIRNAME)
        candidate_roots.append(_SHARED_WORKSPACE_ROOT / _SHARED_SOCKET_DIRNAME)
        if support_path.name == ".moonmind" and len(support_path.parents) >= 3:
            candidate_roots.append(support_path.parents[2] / _SHARED_SOCKET_DIRNAME)
        candidate_roots.append(support_path.parent / _SHARED_SOCKET_DIRNAME)

    for root in dict.fromkeys(candidate_roots):
        path = root / digest / "github.sock"
        try:
            if support_path is not None and not support_path.is_relative_to(root.parent):
                continue
        except ValueError:
            continue
        if len(str(path).encode("utf-8")) < _BROKER_SOCKET_PATH_MAX_BYTES:
            return str(path)

    socket_root_path = Path("/tmp")
    if not socket_root_path.is_dir():
        socket_root_path = Path(tempfile.gettempdir())
    return str(
        socket_root_path
        / _LEGACY_SHARED_SOCKET_DIRNAME
        / digest
        / "github.sock"
    )


def render_gh_wrapper_script(*, socket_path: str, real_gh_path: str | None = None) -> str:
    """Render a self-contained gh wrapper for agent workspaces."""

    return (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import shutil\n"
        "import socket\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        f"SOCKET_PATH = {socket_path!r}\n"
        f"REAL_GH_PATH = {real_gh_path!r}\n"
        f"TIMEOUT_SECONDS = {_BROKER_SOCKET_TIMEOUT_SECONDS!r}\n"
        "\n"
        "def request_token():\n"
        "    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "    client.settimeout(TIMEOUT_SECONDS)\n"
        "    try:\n"
        "        client.connect(SOCKET_PATH)\n"
        "        client.sendall(json.dumps({'command': 'github_token'}).encode('utf-8') + b'\\n')\n"
        "        response = bytearray()\n"
        "        while True:\n"
        "            chunk = client.recv(4096)\n"
        "            if not chunk:\n"
        "                break\n"
        "            response.extend(chunk)\n"
        "            if b'\\n' in chunk:\n"
        "                break\n"
        "        if not response:\n"
        "            raise RuntimeError('GitHub auth broker returned no response')\n"
        "        parsed = json.loads(response.decode('utf-8').strip())\n"
        "        if not parsed.get('ok'):\n"
        "            raise RuntimeError(str(parsed.get('error') or 'broker request failed'))\n"
        "        return str(parsed.get('token') or '')\n"
        "    finally:\n"
        "        client.close()\n"
        "\n"
        "def resolve_real_gh():\n"
        "    if REAL_GH_PATH:\n"
        "        return str(REAL_GH_PATH)\n"
        "    wrapper_dir = Path(sys.argv[0]).resolve().parent\n"
        "    path_parts = [\n"
        "        item for item in os.environ.get('PATH', '').split(os.pathsep)\n"
        "        if item and Path(item).resolve() != wrapper_dir\n"
        "    ]\n"
        "    resolved = shutil.which('gh', path=os.pathsep.join(path_parts))\n"
        "    if not resolved:\n"
        "        raise RuntimeError('Unable to locate real gh binary in managed session PATH')\n"
        "    return str(resolved)\n"
        "\n"
        "def main():\n"
        "    token = request_token()\n"
        "    env = dict(os.environ)\n"
        "    env.pop('GH_TOKEN', None)\n"
        "    env['GITHUB_TOKEN'] = token\n"
        "    real_gh = resolve_real_gh()\n"
        "    os.execvpe(real_gh, [real_gh, *sys.argv[1:]], env)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def render_git_credential_helper_script(*, socket_path: str) -> str:
    """Render a self-contained git credential helper for agent workspaces."""

    return (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import socket\n"
        "import sys\n"
        "\n"
        f"SOCKET_PATH = {socket_path!r}\n"
        f"TIMEOUT_SECONDS = {_BROKER_SOCKET_TIMEOUT_SECONDS!r}\n"
        "\n"
        "def request_token():\n"
        "    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "    client.settimeout(TIMEOUT_SECONDS)\n"
        "    try:\n"
        "        client.connect(SOCKET_PATH)\n"
        "        client.sendall(json.dumps({'command': 'github_token'}).encode('utf-8') + b'\\n')\n"
        "        response = bytearray()\n"
        "        while True:\n"
        "            chunk = client.recv(4096)\n"
        "            if not chunk:\n"
        "                break\n"
        "            response.extend(chunk)\n"
        "            if b'\\n' in chunk:\n"
        "                break\n"
        "        if not response:\n"
        "            raise RuntimeError('GitHub auth broker returned no response')\n"
        "        parsed = json.loads(response.decode('utf-8').strip())\n"
        "        if not parsed.get('ok'):\n"
        "            raise RuntimeError(str(parsed.get('error') or 'broker request failed'))\n"
        "        return str(parsed.get('token') or '')\n"
        "    finally:\n"
        "        client.close()\n"
        "\n"
        "def main():\n"
        "    operation = str(sys.argv[1] if len(sys.argv) > 1 else '').strip().lower()\n"
        "    if operation not in {'get', 'fill'}:\n"
        "        return 0\n"
        "    request = {}\n"
        "    for raw_line in sys.stdin:\n"
        "        line = raw_line.rstrip('\\n')\n"
        "        if not line:\n"
        "            break\n"
        "        key, _, value = line.partition('=')\n"
        "        request[key] = value\n"
        "    host = str(request.get('host') or '').strip().lower()\n"
        "    protocol = str(request.get('protocol') or '').strip().lower()\n"
        "    if host != 'github.com' or (protocol and protocol != 'https'):\n"
        "        return 0\n"
        "    token = request_token()\n"
        "    sys.stdout.write('username=x-access-token\\n')\n"
        "    sys.stdout.write(f'password={token}\\n\\n')\n"
        "    sys.stdout.flush()\n"
        "    return 0\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )

@dataclass(slots=True)
class GitHubAuthBrokerHandle:
    """Per-run in-memory GitHub token broker."""

    run_id: str
    socket_path: str
    server: asyncio.AbstractServer
    serve_task: asyncio.Task[None]
    token: str

    async def close(self) -> None:
        """Stop serving broker requests and remove the socket."""
        self.server.close()
        await self.server.wait_closed()
        self.serve_task.cancel()
        try:
            await self.serve_task
        except asyncio.CancelledError:
            # Task cancellation is expected here during shutdown.
            pass
        try:
            os.remove(self.socket_path)
        except OSError:
            # Socket removal is best-effort cleanup; shutdown should continue.
            pass

class GitHubAuthBrokerManager:
    """Owns per-run GitHub auth brokers inside the worker process."""

    def __init__(self) -> None:
        self._handles: dict[str, GitHubAuthBrokerHandle] = {}

    async def start(
        self,
        *,
        run_id: str,
        token: str,
        socket_path: str,
    ) -> GitHubAuthBrokerHandle:
        """Start or replace a broker for one managed run."""
        await self.stop(run_id)

        path = Path(socket_path)
        shared_root = path.parent.parent
        if shared_root.name in {
            _LEGACY_SHARED_SOCKET_DIRNAME,
            _SHARED_SOCKET_DIRNAME,
        }:
            shared_root.mkdir(parents=True, exist_ok=True, mode=0o711)
            shared_root.chmod(0o711)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        try:
            path.unlink()
        except FileNotFoundError:
            # It's fine if the socket file does not exist yet.
            pass

        async def _handle_client(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                request_line = await asyncio.wait_for(
                    reader.readline(),
                    timeout=_BROKER_SOCKET_TIMEOUT_SECONDS,
                )
                request = json.loads(request_line.decode("utf-8"))
                command = str(request.get("command") or "").strip()
                if command == "ping":
                    response: dict[str, Any] = {"ok": True}
                elif command == "github_token":
                    response = {"ok": True, "token": token}
                else:
                    response = {"ok": False, "error": f"unsupported command: {command}"}
                writer.write((json.dumps(response) + "\n").encode("utf-8"))
                await writer.drain()
            except Exception as exc:  # noqa: BLE001
                try:
                    writer.write(
                        (
                            json.dumps({"ok": False, "error": str(exc)[:200]}) + "\n"
                        ).encode("utf-8")
                    )
                    await writer.drain()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass

        server = await asyncio.start_unix_server(_handle_client, path=str(path))
        os.chmod(path, 0o600)
        serve_task = asyncio.create_task(server.serve_forever())
        handle = GitHubAuthBrokerHandle(
            run_id=run_id,
            socket_path=str(path),
            server=server,
            serve_task=serve_task,
            token=token,
        )
        self._handles[run_id] = handle
        await wait_for_broker_socket(str(path))
        return handle

    async def stop(self, run_id: str) -> None:
        """Stop the broker for one managed run when present."""
        handle = self._handles.pop(run_id, None)
        if handle is None:
            return
        await handle.close()

async def wait_for_broker_socket(socket_path: str) -> None:
    """Poll until the broker socket exists and answers ping."""
    deadline = asyncio.get_running_loop().time() + _BROKER_SOCKET_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        if not Path(socket_path).exists():
            await asyncio.sleep(0.05)
            continue
        try:
            if await _async_request(socket_path, command="ping") == "":
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await asyncio.sleep(0.05)
            continue
    raise RuntimeError(
        f"Timed out waiting for GitHub auth broker socket at {socket_path}"
    ) from last_error

async def _async_request(socket_path: str, *, command: str) -> str:
    """Fetch one broker response using asyncio Unix sockets."""
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        payload = json.dumps({"command": command}).encode("utf-8") + b"\n"
        writer.write(payload)
        await writer.drain()
        response = await asyncio.wait_for(
            reader.readline(),
            timeout=_BROKER_SOCKET_TIMEOUT_SECONDS,
        )
        if not response:
            raise RuntimeError("GitHub auth broker returned no response")
        parsed = json.loads(response.decode("utf-8").strip())
        if not parsed.get("ok"):
            raise RuntimeError(str(parsed.get("error") or "broker request failed"))
        return str(parsed.get("token") or "")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass

def request_github_token(socket_path: str, *, command: str = "github_token") -> str:
    """Fetch the GitHub token from the broker over a local Unix socket."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(_BROKER_SOCKET_TIMEOUT_SECONDS)
    try:
        client.connect(socket_path)
        payload = json.dumps({"command": command}).encode("utf-8") + b"\n"
        client.sendall(payload)

        response = bytearray()
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
            if b"\n" in chunk:
                break
        if not response:
            raise RuntimeError("GitHub auth broker returned no response")
        parsed = json.loads(response.decode("utf-8").strip())
        if not parsed.get("ok"):
            raise RuntimeError(str(parsed.get("error") or "broker request failed"))
        return str(parsed.get("token") or "")
    finally:
        client.close()

def _resolve_real_gh_path() -> str:
    """Resolve gh from the container PATH without returning this wrapper."""

    wrapper_path = Path(sys.argv[0]).resolve()
    wrapper_dir = wrapper_path.parent
    path_parts = [
        item
        for item in os.environ.get("PATH", "").split(os.pathsep)
        if item and Path(item).resolve() != wrapper_dir
    ]
    resolved = shutil.which("gh", path=os.pathsep.join(path_parts))
    if not resolved:
        raise RuntimeError("Unable to locate real gh binary in managed session PATH")
    return resolved

def run_gh_wrapper(*, socket_path: str, real_gh_path: str | None = None) -> int:
    """Exec the real gh binary with a token fetched from the local broker."""
    resolved_gh_path = str(real_gh_path or _resolve_real_gh_path())
    token = request_github_token(socket_path)
    env = dict(os.environ)
    env.pop("GH_TOKEN", None)
    env["GITHUB_TOKEN"] = token
    os.execvpe(resolved_gh_path, [resolved_gh_path, *sys.argv[1:]], env)
    return 0

def run_git_credential_helper(*, socket_path: str) -> int:
    """Respond to git credential-helper requests with brokered GitHub auth."""
    operation = str(sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if operation not in {"get", "fill"}:
        return 0

    request: dict[str, str] = {}
    for raw_line in sys.stdin:
        line = raw_line.rstrip("\n")
        if not line:
            break
        key, _, value = line.partition("=")
        request[key] = value

    host = str(request.get("host") or "").strip().lower()
    protocol = str(request.get("protocol") or "").strip().lower()
    if host != "github.com" or (protocol and protocol != "https"):
        return 0

    token = request_github_token(socket_path)
    sys.stdout.write("username=x-access-token\n")
    sys.stdout.write(f"password={token}\n\n")
    sys.stdout.flush()
    return 0
