"""Metadata-only reservation fixtures for the real GitHub projection writer."""

import json
import os
import subprocess
from uuid import NAMESPACE_URL, uuid5

from moonmind.omnigent.host_services.github_credentials import github_projection_script


def projection_reservation(lease_id, revision=1):
    return {
        "ownerRef": f"host-lease:{lease_id}",
        "revision": revision,
        "reservationId": str(uuid5(NAMESPACE_URL, f"{lease_id}:{revision}")),
    }


def reserve_projection(directory, reservation):
    subprocess.run(
        [
            "/bin/sh",
            "-ceu",
            github_projection_script(str(directory), action="reserve"),
            "--",
            str(os.getuid()),
            str(os.getgid()),
            "github.com",
            json.dumps(reservation),
        ],
        check=True,
        capture_output=True,
    )
