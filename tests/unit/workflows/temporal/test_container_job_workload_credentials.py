"""Worker delivery of scoped workload credentials (#5037).

The Docker daemon, secret store and STS are deterministic fixtures. Seeded
secret bytes are searched for in every launch argument, request payload,
published evidence, live log and lease record.
"""

from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moonmind.auth.secret_refs import SecretMissingError
from moonmind.config.container_backend_settings import (
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
    ContainerJobBackendError,
    ContainerJobFailureClass,
    ContainerJobSpec,
    ContainerJobSubmitRequest,
    OwnerIdentity,
    WorkloadCredentialAuthorization,
)
from moonmind.security.workload_credentials import (
    READ_ONLY_POLICY_ARNS,
    CloudSession,
    CredentialIssueError,
    authorize_workload_credentials,
)
from moonmind.security.workload_profiles import (
    WORKLOAD_PROFILES_ENV_KEY,
    AdmittedAssessmentScope,
    resolve_profile_job,
)
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend

JOB = "container-job:" + "c" * 32
OWNER = OwnerIdentity(principalId="operator", principalType="user")
IMAGE = "sha256:" + "d" * 64
ROLE = "arn:aws:iam::123456789012:role/audit"
SOURCE = {
    "accessKeyId": "AKIASEEDSOURCE0001",
    "secretAccessKey": "seed-source-secret-bytes",
}
APP_VALUE = "seed-app-login-bytes"
SEEDS = (
    *SOURCE.values(),
    APP_VALUE,
    "ASIASEEDSESSION",
    "seed-session-secret",
    "seed-session-token",
)

PROFILE = {
    "profileRef": "cloud-audit",
    "imageSourceRef": "cloud-scanner",
    "modes": [
        {
            "name": "audit",
            "actionClass": "cloud-inventory",
            "argv": ["audit", "{target}", "--output=/outputs/report.json"],
            "arguments": [{"name": "target", "type": "input", "required": True}],
        }
    ],
    "credentials": [
        {
            "name": "cloud",
            "purpose": "cloud_inventory",
            "delivery": "file",
            "issuer": "aws-sts",
        },
        {
            "name": "app",
            "purpose": "application_auth",
            "delivery": "env",
            "environmentName": "APP_LOGIN",
        },
    ],
    "outputs": [{"name": "report", "relativePath": "report.json"}],
    "maxCpuMillis": 2000,
    "maxMemoryMiB": 2048,
    "maxTimeoutSeconds": 3600,
}
SETTINGS = resolve_container_backend_settings(
    {
        IMAGE_SOURCES_ENV_KEY: json.dumps(
            [{"sourceRef": "cloud-scanner", "image": "ghcr.io/x/prowler:1"}]
        ),
        WORKLOAD_PROFILES_ENV_KEY: json.dumps([PROFILE]),
    }
)


def scope(expires_in=timedelta(hours=2)):
    expires = (datetime.now(timezone.utc) + expires_in).isoformat()
    return AdmittedAssessmentScope.model_validate(
        {
            "assessmentRef": "assessment-1",
            "profileRefs": ["cloud-audit"],
            "actionClasses": ["cloud-inventory"],
            "targets": [{"workspacePath": "config"}],
            "maxJobs": 4,
            "maxJobSeconds": 3600,
            "evidenceIntent": "cloud inventory",
            "grants": [
                {
                    "ref": "db://aws-audit",
                    "purpose": "cloud_inventory",
                    "account": "123456789012",
                    "profileRefs": ["cloud-audit"],
                    "expiresAt": expires,
                    "roleArn": ROLE,
                    "revision": 3,
                },
                {
                    "ref": "db://app-login",
                    "purpose": "application_auth",
                    "account": "https://app.test",
                    "profileRefs": ["cloud-audit"],
                    "expiresAt": expires,
                },
            ],
        }
    )


class Secrets:
    def __init__(self):
        self.store = {
            "db://aws-audit": (3, json.dumps(SOURCE)),
            "db://app-login": (1, APP_VALUE),
        }
        self.calls = []

    async def __call__(self, ref, revision):
        self.calls.append((ref, revision))
        if ref not in self.store or (
            revision is not None and self.store[ref][0] != revision
        ):
            raise SecretMissingError("not active at that revision")
        return self.store[ref][1]


class Sts:
    def __init__(self):
        self.calls = []
        self.errors = []
        self.lifetime = None

    async def issue(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        n = len(self.calls)
        lifetime = self.lifetime or timedelta(seconds=kwargs["duration_seconds"])
        return CloudSession(
            access_key_id=f"ASIASEEDSESSION{n}",
            secret_access_key=f"seed-session-secret-{n}",
            session_token=f"seed-session-token-{n}",
            expiration=datetime.now(timezone.utc) + lifetime,
        )


class Fixture:
    def __init__(self, tmp_path, *, admitted=None, credentials=None):
        self.root = tmp_path / "workspaces"
        repository = self.root / "repo"
        (repository / "config").mkdir(parents=True)
        (repository / "config" / "accounts.json").write_text("{}")
        selected = (
            credentials
            if credentials is not None
            else [
                {
                    "slot": "cloud",
                    "credentialRef": "db://aws-audit",
                    "account": "123456789012",
                },
                {
                    "slot": "app",
                    "credentialRef": "db://app-login",
                    "account": "https://app.test",
                },
            ]
        )
        spec = resolve_profile_job(
            ContainerJobSpec.model_validate(
                {
                    "workloadProfile": {
                        "profileRef": "cloud-audit",
                        "mode": "audit",
                        "arguments": {"target": "config"},
                        "assessmentRef": "assessment-1",
                        "credentials": selected,
                    },
                    "workspaceRef": {"kind": "external_state", "artifactRef": "art_ws"},
                    "inputAttachments": [{"name": "config", "workspacePath": "config"}],
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                    "timeoutSeconds": 1800,
                }
            ),
            SETTINGS.workload_profiles,
        )
        decisions = authorize_workload_credentials(
            spec,
            admitted or scope(),
            SETTINGS.workload_profiles,
            principal="user:operator",
            now=datetime.now(timezone.utc),
        )
        self.request = ContainerJobActivityRequest(
            jobId=JOB,
            owner=OWNER,
            ownershipToken=f"{JOB}:v1",
            request=ContainerJobSubmitRequest(
                idempotencyKey="k", source={"source": "mcp"}, spec=spec
            ),
            workloadCredentials=tuple(
                WorkloadCredentialAuthorization.model_validate({**d, "jobId": JOB})
                for d in decisions
            ),
            resolvedWorkspaceRef=str(repository),
            resolvedImageRef=IMAGE,
        )
        self.commands = []
        self.published = {}
        self.logs = (b"", b"")
        self.running = True
        self.container_exists = True
        self.secrets = Secrets()
        self.sts = Sts()
        self.backend = DockerContainerJobBackend(
            workspace_root=self.root,
            settings=SETTINGS,
            command_runner=self.daemon,
            evidence_publisher=self.publish,
            secret_resolver=self.secrets,
            cloud_session_issuer=self.sts,
            log_spool_root=tmp_path / "live",
        )
        self.storage = storage_for(self.root, JOB)

    async def daemon(self, argv):
        argv = tuple(argv)
        self.commands.append(argv)
        if argv[:3] == ("inspect", "--format", "{{json .State}}"):
            return 0, json.dumps({"Running": self.running, "ExitCode": 0}).encode(), b""
        if argv[0] == "inspect":
            return 1, b"", b"No such container"
        if argv[0] == "logs":
            return 0, *self.logs
        return 0, b"", b""

    async def publish(self, _request, name, payload):
        self.published[name] = payload
        return f"art_{len(self.published):04d}"

    def created(self):
        return next(command for command in self.commands if command[0] == "create")

    def credential_root(self) -> Path:
        return self.storage.workload_credentials


def assert_no_seed(blob) -> None:
    text = blob if isinstance(blob, str) else blob.decode(errors="replace")
    for seed in SEEDS:
        assert seed not in text


@pytest.mark.asyncio
async def test_launch_delivers_only_admitted_values_outside_argv_and_history(
    tmp_path, monkeypatch
):
    # Ambient worker cloud credentials are never inherited by the job.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAAMBIENTWORKER")
    fixture = Fixture(tmp_path)
    await fixture.backend.create_container(fixture.request)

    created = fixture.created()
    assert_no_seed(" ".join(created))
    assert_no_seed(fixture.request.model_dump_json(by_alias=True))
    assert "AKIAAMBIENTWORKER" not in " ".join(created)
    assert not any(
        ".codex" in arg or ".claude" in arg or "/home/" in arg for arg in created
    )
    assert "AWS_SHARED_CREDENTIALS_FILE=/run/moonmind-credentials/cloud" in created
    assert "AWS_EC2_METADATA_DISABLED=true" in created
    mounts = [created[i + 1] for i, arg in enumerate(created) if arg == "--mount"]
    assert any(
        m.endswith("dst=/run/moonmind-credentials,readonly")
        and "workload-credentials/files" in m
        for m in mounts
    )

    root = fixture.credential_root()
    env_file = Path(created[created.index("--env-file") + 1])
    assert env_file == root / "env"
    assert env_file.read_text() == f"APP_LOGIN={APP_VALUE}\n"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    session_file = root / "files" / "cloud"
    assert stat.S_IMODE(session_file.stat().st_mode) == 0o444
    assert "aws_session_token = seed-session-token-1" in session_file.read_text()
    # The long-lived source key stays in the secret store; only the session
    # reaches the job, and the lease record carries no value.
    for path in root.rglob("*"):
        if path.is_file():
            assert SOURCE["secretAccessKey"] not in path.read_text()
    assert_no_seed((root / "lease.json").read_text())

    assert fixture.secrets.calls == [("db://aws-audit", 3), ("db://app-login", None)]
    [call] = fixture.sts.calls
    assert call["source"] == SOURCE and call["role_arn"] == ROLE
    assert call["policy_arns"] == READ_ONLY_POLICY_ARNS
    assert call["duration_seconds"] == 1800


@pytest.mark.parametrize("rotated", ["db://aws-audit", "db://app-login"])
@pytest.mark.asyncio
async def test_revoked_or_rotated_reference_fails_before_the_container(
    tmp_path, rotated
):
    fixture = Fixture(tmp_path)
    if rotated == "db://aws-audit":
        fixture.secrets.store[rotated] = (4, json.dumps(SOURCE))
    else:
        del fixture.secrets.store[rotated]
    with pytest.raises(ContainerJobBackendError) as failed:
        await fixture.backend.create_container(fixture.request)
    assert failed.value.failure_class is ContainerJobFailureClass.CREDENTIAL_UNRESOLVED
    assert not any(command[0] == "create" for command in fixture.commands)
    assert not (fixture.credential_root() / "env").exists()


@pytest.mark.asyncio
async def test_expired_grant_and_foreign_decision_never_resolve(tmp_path):
    fixture = Fixture(tmp_path)
    expired = tuple(
        item.model_copy(
            update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )
        for item in fixture.request.workload_credentials
    )
    sibling = tuple(
        item.model_copy(update={"job_id": "container-job:" + "e" * 32})
        for item in fixture.request.workload_credentials
    )
    for decisions, failure in (
        (expired, ContainerJobFailureClass.CREDENTIAL_EXPIRED),
        (sibling, ContainerJobFailureClass.AUTHORIZATION),
        (None, ContainerJobFailureClass.AUTHORIZATION),
    ):
        request = fixture.request.model_copy(update={"workload_credentials": decisions})
        with pytest.raises(ContainerJobBackendError) as failed:
            await fixture.backend.create_container(request)
        assert failed.value.failure_class is failure
    assert fixture.secrets.calls == [] and fixture.sts.calls == []
    assert not any(command[0] == "create" for command in fixture.commands)


@pytest.mark.asyncio
async def test_issuance_failure_keeps_its_code_and_uncertain_issuance_is_reconciled(
    tmp_path,
):
    fixture = Fixture(tmp_path)
    fixture.sts.errors = [CredentialIssueError("AccessDenied")]
    with pytest.raises(ContainerJobBackendError, match="AccessDenied") as denied:
        await fixture.backend.create_container(fixture.request)
    assert (
        denied.value.failure_class
        is ContainerJobFailureClass.CREDENTIAL_ISSUANCE_FAILED
    )
    lease = json.loads((fixture.credential_root() / "lease.json").read_text())
    assert lease["sessions"]["cloud"]["failureCode"] == "AccessDenied"
    assert not any(command[0] == "create" for command in fixture.commands)

    # A lost answer leaves the outcome unknown; the retry records it and
    # issues a fresh bounded session instead of adopting the unknown one.
    fixture.sts.errors = [CredentialIssueError("ReadTimeout", uncertain=True)]
    with pytest.raises(ContainerJobBackendError):
        await fixture.backend.create_container(fixture.request)
    budget = json.loads((fixture.credential_root() / "lease.json").read_text())[
        "budgetEndsAt"
    ]
    await fixture.backend.create_container(fixture.request)
    lease = json.loads((fixture.credential_root() / "lease.json").read_text())
    assert lease["sessions"]["cloud"]["state"] == "issued"
    assert lease["sessions"]["cloud"]["uncertainIssuances"] == 1
    assert "failureCode" not in lease["sessions"]["cloud"]
    # Retries never extend the job's credential budget.
    assert lease["budgetEndsAt"] == budget


@pytest.mark.asyncio
async def test_running_job_renews_near_expiry_and_renewal_failure_is_typed(tmp_path):
    fixture = Fixture(tmp_path)
    fixture.sts.lifetime = timedelta(seconds=120)
    await fixture.backend.create_container(fixture.request)
    fixture.request.container_ref = "job-container"
    session_file = fixture.credential_root() / "files" / "cloud"
    assert "seed-session-token-1" in session_file.read_text()

    result = await fixture.backend.observe_container(fixture.request)
    assert result.running is True
    assert "seed-session-token-2" in session_file.read_text()
    assert stat.S_IMODE(session_file.stat().st_mode) == 0o444
    lease = json.loads((fixture.credential_root() / "lease.json").read_text())
    assert lease["sessions"]["cloud"]["renewals"] == 1

    fixture.sts.errors = [CredentialIssueError("ExpiredToken")]
    with pytest.raises(ContainerJobBackendError, match="ExpiredToken") as failed:
        await fixture.backend.observe_container(fixture.request)
    assert (
        failed.value.failure_class
        is ContainerJobFailureClass.CREDENTIAL_ISSUANCE_FAILED
    )

    # A session with time left is not reissued.
    fixture.sts.lifetime = timedelta(hours=1)
    await fixture.backend.create_container(fixture.request)
    calls = len(fixture.sts.calls)
    await fixture.backend.observe_container(fixture.request)
    assert len(fixture.sts.calls) == calls


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "canceled"])
@pytest.mark.asyncio
async def test_evidence_redacts_delivered_values_and_cleanup_removes_them(
    tmp_path, terminal
):
    fixture = Fixture(tmp_path)
    await fixture.backend.create_container(fixture.request)
    request = fixture.request
    request.container_ref = "job-container"
    leaked = f"login {APP_VALUE} session seed-session-token-1 key ASIASEEDSESSION1"
    fixture.logs = (
        f"2026-10-27T00:00:01.000000000Z {leaked}\n".encode(),
        f"2026-10-27T00:00:02.000000000Z {leaked}\n".encode(),
    )
    await fixture.backend.observe_container(request)
    for path in (tmp_path / "live").rglob("*"):
        if path.is_file():
            assert_no_seed(path.read_bytes())

    fixture.logs = (leaked.encode(), leaked.encode())
    (fixture.storage.outputs / "report.json").write_text(
        json.dumps({"finding": "partial", "echo": leaked})
    )
    request.terminal_state = terminal
    request.exit_code = 0 if terminal == "succeeded" else 1
    result = await fixture.backend.publish_evidence(request)
    assert result.logs_ref and result.artifacts_ref
    for name, payload in fixture.published.items():
        assert_no_seed(payload)
    report = json.loads(fixture.published[f"{JOB}-output-report.json"])
    assert report["finding"] == "partial"
    diagnostics = next(
        json.loads(p) for n, p in fixture.published.items() if "diagnostics" in n
    )
    assert diagnostics["workloadCredentials"]["cloud"]["state"] == "issued"
    # The container has exited: delivered values are gone before publication.
    assert not (fixture.credential_root() / "files").exists()
    assert not (fixture.credential_root() / "env").exists()

    request.publication = AuxiliaryOutcome(state="succeeded")
    cleaned = await fixture.backend.cleanup(request)
    assert cleaned.cleanup_succeeded is True
    assert not fixture.credential_root().exists()
    # The operator's stored long-lived credential is untouched.
    assert fixture.secrets.store["db://aws-audit"] == (3, json.dumps(SOURCE))


@pytest.mark.asyncio
async def test_restart_redelivers_and_cleanup_without_publication_still_removes(
    tmp_path,
):
    fixture = Fixture(tmp_path)
    await fixture.backend.create_container(fixture.request)
    # A worker restart re-runs create under the same job and ownership.
    await fixture.backend.create_container(fixture.request)
    assert len(fixture.sts.calls) == 2
    assert (
        "seed-session-token-2"
        in (fixture.credential_root() / "files" / "cloud").read_text()
    )
    # Failed publication keeps outputs for recovery, never the credentials.
    (fixture.storage.outputs / "report.json").write_text("{}")
    fixture.request.publication = AuxiliaryOutcome(state="failed")
    cleaned = await fixture.backend.cleanup(fixture.request)
    assert cleaned.cleanup_succeeded is False
    assert (fixture.storage.outputs / "report.json").exists()
    assert not fixture.credential_root().exists()


def test_outputs_are_redacted_and_archives_holding_a_value_are_withheld():
    import gzip

    from moonmind.workflows.temporal.container_job_backend import _without_delivered

    values = [APP_VALUE]
    assert _without_delivered(
        f"x {APP_VALUE} y".encode(), "application/json", values
    ) == (b"x [redacted] y")
    clean = gzip.compress(b"inventory")
    assert _without_delivered(clean, "application/gzip", values) == clean
    assert (
        _without_delivered(
            gzip.compress(APP_VALUE.encode()), "application/gzip", values
        )
        is None
    )
    assert _without_delivered(b"not gzip", "application/gzip", values) is None


@pytest.mark.asyncio
async def test_publication_retry_still_redacts_renewed_and_earlier_session_values(
    tmp_path,
):
    fixture = Fixture(tmp_path)
    fixture.sts.lifetime = timedelta(seconds=120)
    await fixture.backend.create_container(fixture.request)
    request = fixture.request
    request.container_ref = "job-container"
    # Renewal replaces the mounted session; the container may still have
    # written the first one.
    await fixture.backend.observe_container(request)
    assert len(fixture.sts.calls) == 2
    leaked = (
        f"{APP_VALUE} seed-session-token-1 seed-session-secret-1 "
        "seed-session-token-2 ASIASEEDSESSION2"
    )
    fixture.logs = (leaked.encode(), leaked.encode())
    (fixture.storage.outputs / "report.json").write_text(json.dumps({"e": leaked}))
    request.terminal_state = "succeeded"
    request.exit_code = 0

    publish = fixture.publish
    failures = [OSError("artifact store unavailable")]

    async def flaky(*args):
        if failures:
            raise failures.pop()
        return await publish(*args)

    fixture.backend._publish = flaky
    with pytest.raises(OSError):
        await fixture.backend.publish_evidence(request)
    # Nothing stays mounted for the exited container.
    assert not (fixture.credential_root() / "files").exists()
    assert not (fixture.credential_root() / "env").exists()
    # The Activity retry publishes everything, still without a value.
    result = await fixture.backend.publish_evidence(request)
    assert result.logs_ref and result.artifacts_ref
    for payload in fixture.published.values():
        assert_no_seed(payload)

    request.publication = AuxiliaryOutcome(state="succeeded")
    assert (await fixture.backend.cleanup(request)).cleanup_succeeded is True
    assert not fixture.credential_root().exists()


def _rewrite_lease(fixture, *, budget_in, expires_in):
    path = fixture.credential_root() / "lease.json"
    lease = json.loads(path.read_text())
    now = datetime.now(timezone.utc)
    lease["budgetEndsAt"] = (now + budget_in).isoformat()
    lease["sessions"]["cloud"]["expiresAt"] = (now + expires_in).isoformat()
    path.write_text(json.dumps(lease))


@pytest.mark.asyncio
async def test_session_covering_the_job_or_outlasting_its_grant_is_kept(tmp_path):
    fixture = Fixture(tmp_path)
    await fixture.backend.create_container(fixture.request)
    fixture.request.container_ref = "job-container"
    calls = len(fixture.sts.calls)

    # Near expiry, but the session already lasts through the job budget.
    _rewrite_lease(
        fixture, budget_in=timedelta(seconds=200), expires_in=timedelta(seconds=201)
    )
    assert (await fixture.backend.observe_container(fixture.request)).running
    assert len(fixture.sts.calls) == calls

    # The grant ends too soon for STS to issue another session: the current
    # one keeps serving the job until it expires instead of failing early.
    _rewrite_lease(
        fixture, budget_in=timedelta(seconds=400), expires_in=timedelta(seconds=200)
    )
    request = fixture.request.model_copy(
        update={
            "workload_credentials": tuple(
                item.model_copy(
                    update={
                        "expires_at": datetime.now(timezone.utc)
                        + timedelta(seconds=250)
                    }
                )
                for item in fixture.request.workload_credentials
            )
        }
    )
    secret_calls = len(fixture.secrets.calls)
    assert (await fixture.backend.observe_container(request)).running
    assert len(fixture.sts.calls) == calls
    assert len(fixture.secrets.calls) == secret_calls
    lease = json.loads((fixture.credential_root() / "lease.json").read_text())
    assert lease["sessions"]["cloud"]["state"] == "issued"
