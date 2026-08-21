#!/usr/bin/env python3
"""Digest exact route-inventory inputs from inside one deployable image."""

from __future__ import annotations

import argparse

from moonmind.omnigent.native_ui_route_inventory import exact_artifact_role_digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role",
        required=True,
        choices=("omnigent_host", "moonmind_facade", "moonmind_harness"),
    )
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    print(exact_artifact_role_digest(args.root, args.role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
