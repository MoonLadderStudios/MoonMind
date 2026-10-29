"""Qualify the default upstream ZAP and Prowler images against local fixtures.

The hermetic journeys stand in for both scanners at the binary boundary. This
runs the images a deployment actually pulls (``stable``, or the operator's
``MOONMIND_WEB_ASSESSMENT_IMAGE`` / ``MOONMIND_CLOUD_POSTURE_IMAGE``) with
MoonMind's rendered ZAP plan and launch command and its rendered Prowler argv,
then classifies the native outputs with the production sanitizers and
summaries. Targets are local: the fixture application behind its allowlisting
gateway inside the ZAP container, and moto's in-process AWS inside the
Prowler container, so every run uses ``--network none``. Nothing is pinned;
a new ``stable`` release is qualified when it arrives.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from moonmind.schemas.repository_scanner_models import (
    CLOUD_OUTPUT_FILES,
    WEB_OUTPUT_FILES,
    CloudAssessment,
    WebAssessment,
)
from moonmind.security.cloud_assessments import (
    CLOUD_IMAGE_SOURCE_REF,
    CloudImage,
    prowler_argv,
    summarize_cloud_assessment,
)
from moonmind.security.web_assessments import (
    build_web_assessment_job,
    render_web_assessment_plan,
    sanitize_messages,
    sanitize_report,
    summarize_web_assessment,
)
from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.temporal import container_job_attachments as attachments

pytestmark = [
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.skipif(
        os.environ.get("MOONMIND_RUN_UPSTREAM_ASSESSMENT_IMAGES") != "1",
        reason="pulls and runs upstream scanner images; dedicated CI workflow",
    ),
]

HERE = Path(__file__).parent
ZAP_IMAGE = os.environ.get("MOONMIND_WEB_ASSESSMENT_IMAGE") or (
    "ghcr.io/zaproxy/zaproxy:stable"
)
PROWLER_IMAGE = os.environ.get("MOONMIND_CLOUD_POSTURE_IMAGE") or (
    "prowlercloud/prowler:stable"
)
# ZAP's own proxy binds 8080. The production launch passes an egress proxy
# on port 3128 to ZAP as its upstream proxy, so the fixture gateway uses it.
APP_PORT = 8091
GATEWAY = "http://127.0.0.1:3128"
ACCOUNT = "123456789012"  # moto's account
CHECKS = [
    "s3_bucket_level_public_access_block",
    "s3_bucket_object_versioning",
    "iam_password_policy_minimum_length_14",
]
# The image's Prowler executable is a console script in its own environment;
# its interpreter is the one that has Prowler and moto.
_PROWLER_PYTHON = (
    'p="$(command -v prowler || ls /home/*/.venv/bin/prowler 2>/dev/null | head -1)"'
    '; [ -n "$p" ] || { echo "prowler executable not found" >&2; exit 97; }'
    '; exec "$(sed -n "1s/^#!//p" "$p")" /qualification/driver.py "$@"'
)


def _excerpt(text: str, limit: int = 4000) -> str:
    text = redact_sensitive_text(text or "")
    return text if len(text) <= limit else "...\n" + text[-limit:]


def run_image(image, args, *, mounts, timeout, environment=()) -> int:
    command = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh"]
    for source, target in mounts.items():
        command += ["--volume", f"{source}:{target}"]
    for item in environment:
        command += ["--env", item]
    try:
        done = subprocess.run(
            [*command, image, "-c", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"{image} exceeded {timeout}s:\n"
            + _excerpt(f"{exc.stdout or ''}{exc.stderr or ''}")
        )
    if done.returncode == 97:
        pytest.fail(f"{image} cannot host the fixture:\n{_excerpt(done.stderr)}")
    print(f"{image} exited {done.returncode}:\n{_excerpt(done.stdout + done.stderr)}")
    return done.returncode


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


@pytest.mark.parametrize(
    ("route", "expected"), [("/search", "findings"), ("/fixed/search", "clean")]
)
def test_zap_image_assesses_vulnerable_and_hardened_routes(tmp_path, route, expected):
    web = WebAssessment.model_validate(
        {
            "mode": "baseline",
            "assessmentRef": "upstream-zap",
            "origin": f"http://app.example.test:{APP_PORT}",
            "routes": [{"path": route, "methods": ["GET"]}],
            "startPaths": [route],
            "egressPolicyRef": "web-target",
            "timeoutSeconds": 300,
        }
    )
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    outputs.chmod(0o777)
    out = outputs / web.output_directory
    attachments.write_plan(out, render_web_assessment_plan(web))
    command = build_web_assessment_job(web)["command"]
    code = run_image(
        ZAP_IMAGE,
        [
            # The fixture app and its allowlisting gateway run on loopback,
            # then the exact production launch command replaces the shell.
            'python3 /qualification/app.py "$0" 3128 & sleep 2; exec sh "$@"',
            str(APP_PORT),
            *command,
        ],
        mounts={
            outputs: "/outputs",
            HERE / "web_assessment_fixture_app.py": "/qualification/app.py:ro",
        },
        environment=[f"MOONMIND_EGRESS_PROXY={GATEWAY}"],
        timeout=web.timeout_seconds + 120,
    )

    def safe(name, sanitize):
        body = _read(out / WEB_OUTPUT_FILES[name])
        return None if body is None else sanitize(body, [], web.max_report_bytes)

    summary = summarize_web_assessment(
        web,
        report=safe("report", sanitize_report),
        messages=safe("messages", sanitize_messages),
        passive_report=safe("passive-report", sanitize_report),
        passive_messages=safe("passive-messages", sanitize_messages),
        exit_code=code,
        terminal_state="succeeded" if code == 0 else "failed",
        native_ref="art_upstream_zap_report",
    )
    print(f"ZAP {summary.execution.version}: {summary.model_dump_json()[:2000]}")
    assert summary.execution.version, "ZAP report did not name its version"
    assert (summary.execution.plan, summary.coverage.tested_routes) == (
        "completed",
        [f"GET {route}"],
    )
    if expected == "clean":
        assert (summary.status, summary.findings, summary.limitations) == (
            "complete",
            [],
            [],
        )
    else:
        assert summary.findings and summary.result.findings
        assert {f.route for f in summary.findings} == {route}


@pytest.mark.parametrize(
    ("scenario", "outcome"), [("vulnerable", "findings"), ("repaired", "clean")]
)
def test_prowler_image_assesses_open_and_repaired_account(tmp_path, scenario, outcome):
    cloud = CloudAssessment.model_validate(
        {
            "provider": "aws",
            "assessmentRef": "upstream-prowler",
            "account": ACCOUNT,
            "credentialRef": "secret://cloud/audit",
            "regions": ["us-east-1"],
            "checks": CHECKS,
            "egressPolicyRef": "aws-apis",
            "timeoutSeconds": 600,
        }
    )
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    outputs.chmod(0o777)
    out = outputs / cloud.output_directory
    # Prowler opens its log file before creating the output directory.
    attachments.prepare_output_directory(out)
    code = run_image(
        PROWLER_IMAGE,
        [_PROWLER_PYTHON, "driver", scenario, *prowler_argv(cloud)],
        mounts={
            outputs: "/outputs",
            HERE / "upstream_prowler_driver.py": "/qualification/driver.py:ro",
        },
        timeout=cloud.timeout_seconds,
    )
    log = _read(out / CLOUD_OUTPUT_FILES["log"])
    if log is not None:
        # moto does not implement every call AWS does; those lines describe
        # the fixture, not Prowler or the account.
        log = redact_sensitive_text(
            "".join(
                line
                for line in log.decode("utf-8", errors="replace").splitlines(True)
                if "NotImplementedError" not in line
            )
        ).encode()
    summary = summarize_cloud_assessment(
        cloud,
        image=CloudImage(
            sourceRef=CLOUD_IMAGE_SOURCE_REF, requestedReference=PROWLER_IMAGE
        ),
        report=_read(out / CLOUD_OUTPUT_FILES["report"]),
        log=log,
        exit_code=code,
        terminal_state="succeeded" if code == 0 else "failed",
        native_ref="art_upstream_prowler_report",
    )
    print(f"Prowler {summary.execution.version}: {summary.model_dump_json()[:2000]}")
    assert (summary.outcome, summary.status, summary.limitations) == (
        outcome,
        "complete",
        [],
    )
    assert summary.coverage.scanned_accounts == [ACCOUNT]
    if outcome == "findings":
        assert summary.execution.version, "Prowler report did not name its version"
        assert {f.check_id for f in summary.findings} == set(CHECKS)
    else:
        assert summary.findings == [] and summary.coverage.passed >= len(CHECKS)
