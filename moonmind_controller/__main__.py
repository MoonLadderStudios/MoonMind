"""Host-owned entrypoint for the standalone controller process."""

from __future__ import annotations

import argparse

from moonmind_controller.server import serve


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, state_dir=args.state_dir)


if __name__ == "__main__":
    main()
