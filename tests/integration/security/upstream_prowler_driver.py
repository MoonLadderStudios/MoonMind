"""Runs MoonMind's rendered Prowler argv inside the upstream Prowler image.

The interpreter is the image's own Prowler environment, which ships moto.
moto's in-process AWS stands in for the account, so no request leaves the
container (``--network none``). ``vulnerable`` leaves a bucket open and sets
no password policy; ``repaired`` blocks public access, enables versioning
and sets a 14-character minimum. Exit 97 means the image cannot host this
fixture, which is a qualification-harness gap rather than a Prowler result.
"""

from __future__ import annotations

import os
import sys

FIXTURE_UNAVAILABLE = 97
BUCKET = "assessed-bucket"


def main(argv: list[str]) -> int:
    scenario, prowler_args = argv[0], argv[1:]
    os.environ.update(
        AWS_ACCESS_KEY_ID="fixture",
        AWS_SECRET_ACCESS_KEY="fixture",
        AWS_DEFAULT_REGION="us-east-1",
    )
    try:
        import boto3
        from moto import mock_aws
        from prowler.__main__ import prowler
    except ImportError as exc:
        print(f"qualification fixture unavailable: {exc.name}", file=sys.stderr)
        return FIXTURE_UNAVAILABLE
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        if scenario == "repaired":
            s3.put_public_access_block(
                Bucket=BUCKET,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
            s3.put_bucket_versioning(
                Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"}
            )
            boto3.client("iam").update_account_password_policy(MinimumPasswordLength=14)
        sys.argv = ["prowler", *prowler_args]
        try:
            result = prowler()
        except SystemExit as exc:
            result = exc.code
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
