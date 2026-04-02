"""Broker GitHub credentials to local helper scripts without writing PATs to disk."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_BROKER_SOCKET_TIMEOUT_SECONDS = 5.0


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
            pass
        try:
            os.remove(self.socket_path)
        except OSError:
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
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.unlink()
        except FileNotFoundError:
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


def run_gh_wrapper(*, socket_path: str, real_gh_path: str) -> int:
    """Exec the real gh binary with a token fetched from the local broker."""
    token = request_github_token(socket_path)
    env = dict(os.environ)
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    os.execvpe(real_gh_path, [real_gh_path, *sys.argv[1:]], env)
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
