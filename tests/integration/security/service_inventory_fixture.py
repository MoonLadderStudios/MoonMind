"""Controlled TCP peers with bounded connection/request observations for CI."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BANNER = b"220 product=claimed-not-verified neighbor=unadmitted.example.test:9090\r\n"
REDIRECT = (
    b"HTTP/1.1 302 Found\r\nLocation: http://unadmitted.example.test:9090/\r\n"
    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
)
PORTS = (2525, 8080, 8081, 8082, 8083, 9090)


class InventoryFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.counts = {str(port): {"connections": 0, "requests": []} for port in PORTS}
        self.tasks = set()
        self.save()

    def save(self):
        pending = self.root / "counts.pending"
        pending.write_text(json.dumps(self.counts))
        pending.replace(self.root / "counts.json")

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        port = writer.get_extra_info("sockname")[1]
        row = self.counts[str(port)]
        row["connections"] += 1
        self.save()
        try:
            if port == 2525:
                writer.write(BANNER)
                await writer.drain()
            elif port in (8080, 8081):
                request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 4)
                row["requests"].append(request.decode("ascii"))
                self.save()
                writer.write(REDIRECT if port == 8080 else b"not an HTTP status\r\n")
                await writer.drain()
            elif port == 8082:
                await asyncio.sleep(3)
                writer.write(b"220 delayed banner\r\n")
                await writer.drain()
            elif port == 8083:
                # Accept the connection but silently drop application responses.
                await asyncio.wait_for(reader.read(1), 4)
            else:
                writer.write(b"unadmitted endpoint was reached\r\n")
                await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            # Probes may stop before fixture responses when their budget expires.
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                # Peer resets during bounded probe cleanup are already terminal.
                pass
            self.tasks.discard(task)

    async def serve(self):
        servers = [
            await asyncio.start_server(self.handle, "0.0.0.0", port) for port in PORTS
        ]
        (self.root / "ready").touch()
        try:
            await asyncio.Event().wait()
        finally:
            for server in servers:
                server.close()
                await server.wait_closed()
            for task in tuple(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(InventoryFixture(sys.argv[1]).serve())
