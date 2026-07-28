"""Minimal deterministic DNS fixture for restricted-egress conformance."""

from __future__ import annotations

import socket
import struct


APPROVED = "198.18.0.2"
PROHIBITED = "127.0.0.1"
REBIND_QUERIES = 0


def _question(packet: bytes) -> tuple[str, int, bytes]:
    offset = 12
    labels = []
    while packet[offset]:
        size = packet[offset]
        offset += 1
        labels.append(packet[offset : offset + size].decode("ascii"))
        offset += size
    offset += 1
    return ".".join(labels).lower(), struct.unpack("!H", packet[offset : offset + 2])[0], packet[12 : offset + 4]


def _name(value: str) -> bytes:
    return b"".join(bytes((len(label),)) + label.encode("ascii") for label in value.split(".")) + b"\0"


def _a(address: str) -> bytes:
    return b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 0, 4) + socket.inet_aton(address)


def _cname(target: str) -> bytes:
    encoded = _name(target)
    return b"\xc0\x0c" + struct.pack("!HHIH", 5, 1, 0, len(encoded)) + encoded


def answer(packet: bytes) -> bytes:
    global REBIND_QUERIES
    qname, qtype, question = _question(packet)
    records: list[bytes] = []
    if qtype == 1:
        if qname == "allowed.test":
            records = [_a(APPROVED)]
        elif qname == "cname.allowed.test":
            records = [_cname("prohibited.test")]
        elif qname == "prohibited.test":
            records = [_a(PROHIBITED)]
        elif qname == "mixed.allowed.test":
            records = [_a(APPROVED), _a(PROHIBITED)]
        elif qname == "rebind.allowed.test":
            REBIND_QUERIES += 1
            records = [_a(APPROVED if REBIND_QUERIES == 1 else PROHIBITED)]
    flags = 0x8180 if records else 0x8183
    header = packet[:2] + struct.pack("!HHHHH", flags, 1, len(records), 0, 0)
    return header + question + b"".join(records)


def serve() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("0.0.0.0", 53))
    while True:
        request, peer = server.recvfrom(4096)
        try:
            server.sendto(answer(request), peer)
        except (IndexError, UnicodeDecodeError, struct.error):
            continue


if __name__ == "__main__":
    serve()
