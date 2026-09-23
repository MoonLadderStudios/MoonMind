"""Host-owned entrypoint for the standalone controller process."""

from __future__ import annotations

import argparse

from moonmind_controller.server import serve


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # Bound to the container interface by default: the Compose service
    # publishes this port loopback-only on the host, and the endpoint itself
    # requires the deployment-owned bearer secret on every request.
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--lock-dir", default=None)
    parser.add_argument("--stack", default="moonmind")
    parser.add_argument(
        "--project-directory",
        default=None,
        help="Controller-side checkout of the target Compose project "
        "(defaults to $MOONMIND_TARGET_PROJECT_DIR or /target/moonmind).",
    )
    parser.add_argument(
        "--compose-file",
        dest="compose_files",
        action="append",
        default=None,
        help="Target Compose file; repeat for overrides. Defaults to "
        "docker-compose.yaml plus a present override file in the project "
        "directory.",
    )
    args = parser.parse_args(argv)
    serve(
        host=args.host,
        port=args.port,
        state_dir=args.state_dir,
        lock_dir=args.lock_dir,
        stack=args.stack,
        project_directory=args.project_directory,
        compose_files=tuple(args.compose_files) if args.compose_files else None,
    )


if __name__ == "__main__":
    main()
