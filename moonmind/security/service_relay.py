"""Bounded fixed-target transport for a deployment-authorized internal service.

The caller supplies bytes, never a destination. This process is a relay, not an
IP router or general-purpose proxy. Its deployment owns both network interfaces
and the read-only configuration; it requires no network administration rights.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import signal
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_HEALTH = Path("/tmp/moonmind-service-relay.sock")
IMPLEMENTATION_DIGEST = (
    "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
)


def _integer(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("service relay integer is outside its allowed range")
    return value


def _address(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("IPv4 literal required")  # noqa: TRY004
    address = ipaddress.IPv4Address(value)
    if (
        address.is_unspecified
        or address.is_multicast
        or str(address) == "255.255.255.255"
    ):
        raise ValueError("service relay requires a specific unicast IPv4 address")
    return str(address)


@dataclass(frozen=True)
class RelayConfig:
    target_host: str
    target_port: int
    listen_host: str
    listen_port: int
    protocols: tuple[str, ...]
    max_connections: int = 64
    idle_seconds: int = 60
    max_datagram_bytes: int = 65507

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RelayConfig:
        required = {"targetHost", "targetPort", "listenHost", "listenPort", "protocols"}
        optional = {"maxConnections", "idleSeconds", "maxDatagramBytes"}
        if (
            not isinstance(value, dict)
            or set(value) - required - optional
            or required - set(value)
        ):
            raise ValueError(
                "service relay configuration has missing or unknown fields"
            )
        protocols = value["protocols"]
        if (
            not isinstance(protocols, list)
            or not protocols
            or any(p not in ("tcp", "udp") for p in protocols)
            or len(protocols) != len(set(protocols))
        ):
            raise ValueError(
                "service relay protocols must be unique tcp or udp entries"
            )
        result = cls(
            _address(value["targetHost"]),
            _integer(value["targetPort"], 1, 65535),
            _address(value["listenHost"]),
            _integer(value["listenPort"], 1, 65535),
            tuple(sorted(protocols)),
            _integer(value.get("maxConnections", 64), 1, 1024),
            _integer(value.get("idleSeconds", 60), 1, 3600),
            _integer(value.get("maxDatagramBytes", 65507), 1, 65507),
        )
        if (result.target_host, result.target_port) == (
            result.listen_host,
            result.listen_port,
        ):
            raise ValueError("service relay cannot forward to itself")
        return result

    def to_mapping(self) -> dict:
        return {
            "targetHost": self.target_host,
            "targetPort": self.target_port,
            "listenHost": self.listen_host,
            "listenPort": self.listen_port,
            "protocols": list(self.protocols),
            "maxConnections": self.max_connections,
            "idleSeconds": self.idle_seconds,
            "maxDatagramBytes": self.max_datagram_bytes,
        }

    def as_mapping(self) -> dict:
        return self.to_mapping()

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.to_mapping(), sort_keys=True, separators=(",", ":")
        ).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


class FixedTargetRelay:
    def __init__(self, config: RelayConfig, *, health_socket: Path = _DEFAULT_HEALTH):
        self.config = RelayConfig.from_mapping(config.to_mapping())
        self.health_socket = Path(health_socket)
        self._tcp = None
        self._udp = None
        self._health = None
        self._health_inode = None
        self._tcp_tasks: set[asyncio.Task] = set()
        self._udp_sessions: dict[
            tuple, tuple[socket.socket, asyncio.Task, asyncio.TimerHandle]
        ] = {}
        self._closed = False

    @property
    def active_connections(self) -> int:
        return len(self._tcp_tasks) + len(self._udp_sessions)

    async def start(self) -> None:
        if self._closed or self._tcp or self._udp or self._health:
            raise ValueError("service relay cannot be started twice")
        cfg = self.config
        try:
            if "tcp" in cfg.protocols:
                self._tcp = await asyncio.start_server(
                    self._accept_tcp,
                    cfg.listen_host,
                    cfg.listen_port,
                    limit=65536,
                    backlog=cfg.max_connections,
                )
            if "udp" in cfg.protocols:
                self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._udp.setblocking(False)
                self._udp.bind((cfg.listen_host, cfg.listen_port))
                asyncio.get_running_loop().add_reader(
                    self._udp.fileno(), self._read_udp
                )
            await self._start_health()
        except BaseException:
            await self.close()
            raise

    async def _start_health(self) -> None:
        # Never replace another process's socket or an arbitrary path.
        if self.health_socket.exists() or self.health_socket.is_symlink():
            raise ValueError("service relay health socket already exists")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # bind(), unlike asyncio's path convenience API, never unlinks a
            # socket that another process created between our check and bind.
            listener.bind(str(self.health_socket))
            self._health_inode = self.health_socket.stat().st_ino
            self.health_socket.chmod(0o600)
            listener.setblocking(False)
            self._health = await asyncio.start_unix_server(
                self._health_response, sock=listener
            )
        except BaseException:
            listener.close()
            raise

    def _accept_tcp(self, reader, writer) -> None:
        if self._closed or self.active_connections >= self.config.max_connections:
            writer.close()
            return
        task = asyncio.create_task(self._tcp_session(reader, writer))
        self._tcp_tasks.add(task)
        task.add_done_callback(self._tcp_tasks.discard)

    async def _tcp_session(self, reader, writer) -> None:
        upstream = None
        tasks = []
        try:
            remote_reader, upstream = await asyncio.wait_for(
                asyncio.open_connection(
                    self.config.target_host, self.config.target_port, limit=65536
                ),
                self.config.idle_seconds,
            )
            loop = asyncio.get_running_loop()
            touched = loop.time()

            async def pump(source, destination):
                nonlocal touched
                while chunk := await source.read(65536):
                    destination.write(chunk)
                    await destination.drain()
                    touched = loop.time()
                if destination.can_write_eof():
                    destination.write_eof()

            async def expiry():
                while True:
                    remaining = self.config.idle_seconds - (loop.time() - touched)
                    if remaining <= 0:
                        return
                    await asyncio.sleep(remaining)

            tasks = [
                asyncio.create_task(pump(reader, upstream)),
                asyncio.create_task(pump(remote_reader, writer)),
            ]
            pumps = asyncio.gather(*tasks)
            timer = asyncio.create_task(expiry())
            tasks.append(timer)
            try:
                done, _ = await asyncio.wait(
                    [pumps, timer], return_when=asyncio.FIRST_COMPLETED
                )
                if pumps in done:
                    await pumps
            finally:
                pumps.cancel()
                await asyncio.gather(pumps, return_exceptions=True)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for stream in (writer, upstream):
                if stream is not None:
                    stream.close()
                    try:
                        await asyncio.wait_for(stream.wait_closed(), 1)
                    except (OSError, asyncio.TimeoutError):
                        pass

    def _read_udp(self) -> None:
        # Bound callback work and use nonbuffering socket sends: congestion
        # drops datagrams rather than allocating an unbounded transport queue.
        for _ in range(16):
            try:
                data, peer = self._udp.recvfrom(self.config.max_datagram_bytes + 1)
            except (BlockingIOError, OSError):
                return
            if len(data) > self.config.max_datagram_bytes:
                continue
            session = self._udp_sessions.get(peer)
            if session is None:
                if self.active_connections >= self.config.max_connections:
                    continue
                remote = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                remote.setblocking(False)
                try:
                    # Kernel-connected UDP rejects packets from every other
                    # sender. No datagram can supply an alternative target.
                    remote.connect((self.config.target_host, self.config.target_port))
                except OSError:
                    remote.close()
                    continue
                task = asyncio.create_task(self._udp_responses(peer, remote))
                timer = asyncio.get_running_loop().call_later(
                    self.config.idle_seconds, self._drop_udp, peer, remote
                )
                session = (remote, task, timer)
                self._udp_sessions[peer] = session
            try:
                session[0].send(data)
                self._touch_udp(peer)
            except BlockingIOError:
                pass
            except OSError:
                self._drop_udp(peer)

    def _touch_udp(self, peer) -> None:
        session = self._udp_sessions.get(peer)
        if session:
            session[2].cancel()
            timer = asyncio.get_running_loop().call_later(
                self.config.idle_seconds, self._drop_udp, peer, session[0]
            )
            self._udp_sessions[peer] = (session[0], session[1], timer)

    def _drop_udp(self, peer, remote=None) -> None:
        session = self._udp_sessions.get(peer)
        if session is not None and remote is not None and session[0] is not remote:
            return
        session = self._udp_sessions.pop(peer, None)
        if session:
            session[2].cancel()
            session[1].cancel()
            session[0].close()

    async def _udp_responses(self, peer, remote) -> None:
        try:
            while True:
                data = await asyncio.get_running_loop().sock_recv(
                    remote, self.config.max_datagram_bytes + 1
                )
                if (
                    len(data) <= self.config.max_datagram_bytes
                    and self._udp is not None
                ):
                    try:
                        self._udp.sendto(data, peer)
                        self._touch_udp(peer)
                    except BlockingIOError:
                        pass
        except OSError:
            pass
        finally:
            self._drop_udp(peer, remote)

    async def _health_response(self, reader, writer) -> None:
        del reader
        try:
            writer.write(
                (
                    json.dumps(
                        {
                            "configDigest": self.config.digest,
                            "implementationDigest": IMPLEMENTATION_DIGEST,
                        }
                    )
                    + "\n"
                ).encode()
            )
            await asyncio.wait_for(writer.drain(), 1)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()

    async def close(self) -> None:
        self._closed = True
        for server in (self._tcp, self._health):
            if server:
                server.close()
                await server.wait_closed()
        if self._udp:
            asyncio.get_running_loop().remove_reader(self._udp.fileno())
            self._udp.close()
            self._udp = None
        tasks = list(self._tcp_tasks) + [s[1] for s in self._udp_sessions.values()]
        for peer in list(self._udp_sessions):
            self._drop_udp(peer)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tcp_tasks.clear()
        if self._health_inode is not None:
            try:
                if self.health_socket.lstat().st_ino == self._health_inode:
                    self.health_socket.unlink()
            except FileNotFoundError:
                pass


async def check_health(
    config: RelayConfig, health_socket: Path = _DEFAULT_HEALTH
) -> dict:
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(health_socket, limit=256), 1
    )
    try:
        raw = await asyncio.wait_for(reader.readline(), 1)
        result = json.loads(raw)
        if result != {
            "configDigest": config.digest,
            "implementationDigest": IMPLEMENTATION_DIGEST,
        }:
            raise ValueError("service relay live configuration differs")
        return result
    finally:
        writer.close()
        await writer.wait_closed()


async def _run(config, health_socket):
    relay = FixedTargetRelay(config, health_socket=health_socket)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    try:
        await relay.start()
        await stopped.wait()
    finally:
        await relay.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--health-socket", type=Path, default=_DEFAULT_HEALTH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        with args.config.open("rb") as source:
            encoded = source.read(4097)
        if len(encoded) > 4096:
            raise ValueError("service relay configuration is oversized")
        config = RelayConfig.from_mapping(json.loads(encoded))
        if args.check:
            print(json.dumps(asyncio.run(check_health(config, args.health_socket))))
        else:
            asyncio.run(_run(config, args.health_socket))
        return 0
    except (OSError, ValueError, asyncio.TimeoutError):
        # Deliberately omit endpoints and arbitrary file/JSON contents.
        print("service relay configuration or readiness failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
