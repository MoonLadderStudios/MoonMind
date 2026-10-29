#!/usr/bin/env python3
"""Test the real promotion command against an isolated loopback OCI registry.

Small inventory images model successive tool releases. Real scanners and their
qualification run separately on both native architectures in the same workflow.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid
from pathlib import Path

from tools.ci.repository_security_image import ImageError, command, promote


def exercise_updates(evidence: Path) -> None:
    evidence.mkdir(parents=True, exist_ok=True)
    name = "repository-security-update-" + uuid.uuid4().hex[:12]
    command(
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--publish",
        "127.0.0.1::5000",
        "registry:2",
    )
    try:
        port = command("docker", "port", name, "5000/tcp").rsplit(":", 1)[1]
        address = f"localhost:{port}"
        endpoint = "http://" + address
        repository = "qualification/security"
        image = address + "/" + repository

        def request(
            path, data=None, method="GET", content_type="application/octet-stream"
        ):
            url = path if path.startswith("http://") else endpoint + path
            return urllib.request.urlopen(
                urllib.request.Request(
                    url,
                    data=data,
                    method=method,
                    headers={"Content-Type": content_type},
                ),
                timeout=30,
            )

        for attempt in range(30):
            try:
                with request("/v2/"):
                    pass
                break
            except OSError:
                if attempt == 29:
                    raise
                time.sleep(1)

        def blob(data):
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
            with request(f"/v2/{repository}/blobs/uploads/", b"", "POST") as response:
                location = response.headers["Location"]
            with request(
                location + ("&" if "?" in location else "?") + "digest=" + digest,
                data,
                "PUT",
            ):
                pass
            return {"digest": digest, "size": len(data)}

        def candidate(arch, version):
            inventory = {"tools": {"opengrep": {"version": version}}}
            body = json.dumps(inventory).encode()
            source = io.BytesIO()
            with tarfile.open(fileobj=source, mode="w") as archive:
                member = tarfile.TarInfo(
                    "usr/local/share/repository-security/inventory.json"
                )
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
            raw = source.getvalue()
            layer = {
                **blob(gzip.compress(raw)),
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            }
            config = {
                "architecture": arch,
                "os": "linux",
                "config": {},
                "rootfs": {
                    "type": "layers",
                    "diff_ids": ["sha256:" + hashlib.sha256(raw).hexdigest()],
                },
            }
            config_descriptor = {
                **blob(json.dumps(config).encode()),
                "mediaType": "application/vnd.oci.image.config.v1+json",
            }
            manifest = json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": config_descriptor,
                    "layers": [layer],
                }
            ).encode()
            digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
            with request(
                f"/v2/{repository}/manifests/{version}-{arch}",
                manifest,
                "PUT",
                "application/vnd.oci.image.manifest.v1+json",
            ):
                pass
            return {
                "architecture": arch,
                "qualification": "passed",
                "image": image + "@" + digest,
                "inventory": inventory,
                "sbomSha256": hashlib.sha256(body).hexdigest(),
            }

        def default():
            return command(
                "docker", "buildx", "imagetools", "inspect", "--raw", image + ":latest"
            )

        previous = [candidate(arch, "v1.0.0") for arch in ("amd64", "arm64")]
        updated = [candidate(arch, "v1.1.0") for arch in ("amd64", "arm64")]
        promote(previous, image)
        before = default()
        failed = copy.deepcopy(updated)
        failed[1]["qualification"] = "failed"
        try:
            promote(failed, image)
        except ImageError as error:
            failure = str(error)
        else:
            raise AssertionError("failed qualification replaced the default")
        assert default() == before, "failed qualification changed the working manifest"
        promote(updated, image)
        after = default()
        assert after != before
        manifests = json.loads(after)["manifests"]
        assert {row["digest"] for row in manifests} == {
            row["image"].split("@")[1] for row in updated
        }
        assert {row["platform"]["architecture"] for row in manifests} == {
            "amd64",
            "arm64",
        }
        result = {
            "success": "passed",
            "failurePreservedDefault": "passed",
            "failureDiagnostic": failure,
            "previous": previous,
            "updated": updated,
            "previousManifest": json.loads(before),
            "updatedManifest": json.loads(after),
        }
        (evidence / "updates.json").write_text(json.dumps(result, indent=2) + "\n")
    finally:
        original_failure = sys.exc_info()[0] is not None
        cleanup_errors = []
        for args in (("docker", "logs", name), ("docker", "rm", "--force", name)):
            try:
                output = command(*args)
                if args[1] == "logs":
                    (evidence / "registry.log").write_text(output)
            except (OSError, subprocess.CalledProcessError) as error:
                cleanup_errors.append(str(error))
        if cleanup_errors:
            (evidence / "cleanup-errors.json").write_text(
                json.dumps(cleanup_errors) + "\n"
            )
            if not original_failure:
                raise ImageError(
                    "update fixture cleanup failed; see cleanup-errors.json"
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    exercise_updates(parser.parse_args().evidence)
