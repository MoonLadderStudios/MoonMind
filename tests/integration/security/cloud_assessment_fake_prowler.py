"""Prowler 5.46 CLI stand-in for the cloud-posture journey (#5043).

It accepts only the options real Prowler accepts for the rendered argv and,
like Prowler, opens ``--log-file`` before creating its output directory. It
authenticates exactly as Prowler does, with boto3 reading the delivered
``AWS_SHARED_CREDENTIALS_FILE``, against the controlled provider transport
named by ``AWS_ENDPOINT_URL``. It then writes the recorded Prowler outputs in
``tests/fixtures/prowler`` for ``FIXTURE_PROWLER_SCENARIO``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "prowler"
_LOG = (
    '{{"timestamp": "2026-10-28 11:49:28,277", "filename": "{module}.py:1", '
    '"level": "{level}", "module": "{module}", "message": "{message}"}}\n'
)


def recorded_log(name: str) -> str:
    # moto's own unimplemented calls are not part of a real AWS run.
    return "".join(
        line + "\n"
        for line in (FIXTURES / name).read_text().splitlines()
        if "NotImplementedError" not in line
    )


def records(name: str, *statuses: str) -> list[dict]:
    rows = json.loads((FIXTURES / name).read_text())
    return [r for r in rows if not statuses or r["status_code"] in statuses]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="prowler")
    parser.add_argument("provider", choices=["aws"])
    parser.add_argument("--region", nargs="+", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--check", nargs="+")
    selection.add_argument("--service", nargs="+")
    parser.add_argument("--output-formats", nargs="+", choices=["json-ocsf"])
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--output-filename", required=True)
    parser.add_argument("--log-level", choices=["ERROR"])
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--aws-retries-max-attempts", type=int)
    parser.add_argument("--no-banner", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--ignore-exit-code-3", action="store_true")
    args = parser.parse_args(argv)

    scenario = os.environ["FIXTURE_PROWLER_SCENARIO"]
    log = open(args.log_file, "a")  # noqa: SIM115 - Prowler's FileHandler
    os.makedirs(args.output_directory, exist_ok=True)
    report = Path(args.output_directory) / f"{args.output_filename}.ocsf.json"
    try:
        identity = boto3.client("sts", region_name=args.region[0]).get_caller_identity()
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        log.write(
            _LOG.format(
                level="ERROR",
                module="aws_provider",
                message=f"ClientError[1432]: An error occurred ({code}) when "
                "calling the GetCallerIdentity operation: The security token "
                "included in the request is expired",
            )
        )
        log.write(
            _LOG.format(
                level="CRITICAL",
                module="provider",
                message="AWSSessionTokenExpiredError[383]: The provided AWS "
                "Session Token is expired",
            )
        )
        return 1
    account = identity["Account"]

    def write(rows: list[dict], *, cut: int = 0) -> None:
        for row in rows:
            row["cloud"]["account"]["uid"] = account
        body = json.dumps(rows, indent=1).encode()
        report.write_bytes(body[:-cut] if cut else body)

    if scenario == "findings":
        write(records("basic.ocsf.json"))
        log.write(recorded_log("basic-log.txt"))
    elif scenario == "compliant":
        write(records("basic.ocsf.json", "PASS"))
        log.write(recorded_log("basic-log.txt"))
    elif scenario == "partial":
        write(records("deny.ocsf.json"))
        log.write(recorded_log("deny-log.txt"))
    elif scenario == "malformed":
        report.write_bytes(b"Traceback (most recent call last):\n")
        return 1
    elif scenario == "canceled":
        # Stopped while writing: complete records survive before the cut.
        write(records("basic.ocsf.json"), cut=300)
        return 143
    elif scenario == "oversize":
        rows = records("basic.ocsf.json", "PASS") * 400
        write(rows)
    elif scenario == "echo":
        rows = records("basic.ocsf.json", "PASS")
        rows[0]["status_detail"] = Path(
            os.environ["AWS_SHARED_CREDENTIALS_FILE"]
        ).read_text()
        write(rows)
    else:
        raise SystemExit(f"unknown scenario {scenario}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
