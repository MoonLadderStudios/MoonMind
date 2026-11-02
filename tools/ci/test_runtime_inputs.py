"""Hash all test-runtime build inputs, including resolved immutable base images."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path

INPUTS = (
    "api_service/Dockerfile",
    ".dockerignore",
    "pyproject.toml",
    "poetry.lock",
    "api_service/docker/moonmind-docker-wrapper.sh",
    "api_service/docker/cache_temporal_test_server.py",
    "docker/docker-proxy/start.py",
    "docker/docker-proxy/LICENSE.socket-proxy",
)


def verify_input_inventory(dockerfile: str) -> None:
    """A new dependency COPY cannot silently inherit an incomplete cache key."""
    stages: dict[str, dict] = {}
    current = None
    for line in dockerfile.splitlines():
        if not re.match(r"^\s*(FROM|COPY)\s", line, re.IGNORECASE):
            continue
        words = shlex.split(line, comments=True)
        if not words:
            continue
        if words[0].upper() == "FROM":
            words = [word for word in words if not word.startswith("--platform=")]
            if len(words) != 4 or words[2].upper() != "AS":
                raise ValueError(
                    "Unrecognized Dockerfile stage; update the cache inventory"
                )
            current = {"parents": {words[1]}, "files": set()}
            stages[words[3]] = current
        elif words[0].upper() == "COPY":
            if current is None or any(word in {"\\", "["} for word in words):
                raise ValueError("Unrecognized COPY; update the cache inventory")
            source = next(
                (word.split("=", 1)[1] for word in words if word.startswith("--from=")),
                None,
            )
            if source:
                current["parents"].add(source)
            else:
                current["files"].update(
                    word for word in words[1:-1] if not word.startswith("--")
                )
    files, seen = set(), set()

    def visit(name):
        if name not in stages or name in seen:
            return
        seen.add(name)
        files.update(stages[name]["files"])
        for parent in stages[name]["parents"]:
            visit(parent)

    visit("test-runtime")
    expected = set(INPUTS) - {"api_service/Dockerfile", ".dockerignore"}
    if not seen or files != expected:
        raise ValueError(
            f"Test-runtime COPY inventory changed: missing={sorted(files - expected)}, stale={sorted(expected - files)}"
        )


def main() -> None:
    dockerfile = Path("api_service/Dockerfile").read_text()
    verify_input_inventory(dockerfile)
    bases = {}
    for name in ("PYTHON_BASE", "NODE_BASE", "SOCKET_PROXY_BASE"):
        image = re.search(rf"^ARG {name}=(\S+)$", dockerfile, re.MULTILINE)[1]
        digest = subprocess.check_output(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                image,
                "--format",
                "{{.Manifest.Digest}}",
            ],
            text=True,
        ).strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"No immutable base digest for {name}")
        bases[name] = image.split("@")[0] + "@" + digest
    hashes = {
        path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in INPUTS
    }
    payload = {
        "schema": 1,
        "platform": "linux/amd64",
        "testDependencies": True,
        "bases": bases,
        "files": hashes,
    }
    identity = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    print("key=" + identity)
    python_version = re.search(
        r"^ARG PYTHON_BASE=python:(\d+\.\d+)", dockerfile, re.MULTILINE
    )
    if not python_version:
        raise ValueError("Test-runtime Python verification profile is unknown")
    print("python=" + python_version[1])
    for name, image in bases.items():
        print(name + "=" + image)


if __name__ == "__main__":
    main()
