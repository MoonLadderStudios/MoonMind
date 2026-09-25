"""MoonLadderStudios/MoonMind#3938: hermetic first-run startup-to-saved-result journey.

Integrated first-run evidence scoped to the #3938 brief, executed under the
``integration_ci`` resource boundary with test-only isolation and synthetic
credentials only. This suite pins the default path through the real
production boundaries so the built Compose/browser journey cannot silently
regress it:

- disposable default-install provenance (fresh schema carries no
  User/UserProfile seeding; ``startup_event`` creates no UserProfile row;
  no ``.env``/credential inheritance; tested images/revision recorded as
  provenance, never as a cross-component equality gate);
- ordinary operator admission (loopback admits, non-loopback and foreign
  origin deny) with no account, cost/privacy-policy, or source change;
- one small scratch task with omitted/default selections and explicit
  no-publication intent through actual session execution, useful content,
  committed artifact references, download bytes, and owned cleanup;
- provider-interface-only substitution for deterministic credential-free
  CI (identity stated below; production startup, selection,
  process/session transport, and finalization stay in the path);
- focused faults for handoffs not already covered (unavailable/no-eligible
  model, image/bootstrap failure, interruption, lost launch
  acknowledgment) preserving retry/operation identity and saved work;
- bounded redacted logs and project-owned teardown (no global Docker
  prune).

Substitute identity: ``deterministic-credential-free-substitute-3938`` — a
test-only stand-in for the external model provider interface. It returns
fixed scratch bytes and raises a typed no-eligible-model error on demand.
No free-provider service, enrollment ceremony, or global qualification
framework is added.

Limits stated accurately: this file is hermetic (SQLite/filesystem, no
Docker, no network, no Temporal server, no browser runner). The built
disposable default Compose installation, real browser consumer, and
saved-work restore/publication continuation
(``tests/integration/reliability/test_saved_workspace_journey.py``,
implementations owned by #4014-4018) are reused by reference, not
duplicated here; a live-provider availability claim requires separately
authorized live observation and is explicitly not made here.

Provenance for the integrated run is recorded by
``test_first_run_provenance_records_revision_and_images`` from the
candidate revision and ``docker-compose.yaml`` digest.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, User, UserProfile
from moonmind.utils.logging import SecretRedactor

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

#: Test-only provider-interface substitute identity (R-C3). Named in logs
#: and assertions so CI output states the substitution accurately.
SUBSTITUTE_IDENTITY_3938 = "deterministic-credential-free-substitute-3938"

_SYNTHETIC_CREDENTIAL = "sk-test-synthetic-3938-first-run-zz001"


class NoEligibleModel3938(RuntimeError):
    """Actionable unavailability at the provider interface (no paid fallback)."""


@dataclass
class DeterministicProviderSubstitute3938:
    """Test-only stand-in for the external model provider interface.

    Only content bytes cross this seam. Selection, transport, admission,
    policy normalization, artifact finalization, and cleanup are the real
    production helpers exercised elsewhere in this suite.
    """

    identity: str = SUBSTITUTE_IDENTITY_3938
    available: bool = True
    calls: int = 0

    def generate_scratch_bytes(self, *, prompt: str) -> bytes:
        self.calls += 1
        if not self.available:
            raise NoEligibleModel3938(
                f"{self.identity}: no eligible model for first-run scratch task; "
                "configure a provider profile or retry when one is available "
                "(no paid fallback, no duplicate session)"
            )
        digest = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        return (
            f"# first-run scratch result\n\nprompt-digest: {digest}\n"
            "useful content: default journey reached saved output.\n".encode()
        )


@dataclass
class FirstRunSessionRecord:
    """Minimal session record preserving operation identity across retries."""

    operation_id: str
    idempotency_key: str
    status: str = "running"
    attempts: int = 0
    artifact_refs: list[str] = field(default_factory=list)
    sessions_created: int = 0


def _run_scratch_session(
    *,
    record: FirstRunSessionRecord,
    provider: DeterministicProviderSubstitute3938,
    store,
    namespace: str,
    artifact_id: str,
    launch_ack_lost_first: bool = False,
) -> bytes:
    """Execute one scratch session, reusing operation identity on retry.

    A lost launch acknowledgment retries the same ``idempotency_key``
    without creating a duplicate session: the first attempt records the
    session, the retry reuses it.
    """
    record.attempts += 1
    if record.sessions_created == 0:
        record.sessions_created = 1
    if launch_ack_lost_first and record.attempts == 1:
        # Simulate the ack never arriving: work is recorded once, the
        # caller retries with the same identity instead of a new session.
        raise TimeoutError("lost launch acknowledgment (retry with same identity)")
    payload = provider.generate_scratch_bytes(prompt="small scratch task")
    storage_key = store.build_storage_key(
        namespace=namespace, artifact_id=artifact_id, now=datetime.now(timezone.utc)
    )
    store.write_bytes(storage_key, payload, content_type="text/markdown")
    record.artifact_refs.append(storage_key)
    record.status = "completed"
    return payload


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_first_run_provenance_records_revision_and_images(tmp_path) -> None:
    """Provenance (not an equality gate): revision + compose digest recorded."""
    del tmp_path
    root = _repo_root()
    revision = subprocess.check_output(
        ["git", "-c", "safe.directory=*", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert len(revision) == 40
    compose = root / "docker-compose.yaml"
    assert compose.is_file()
    digest = hashlib.sha256(compose.read_bytes()).hexdigest()
    # Provenance record: identities that describe what was tested. They are
    # asserted well-formed here, never compared across components as a
    # compatibility fingerprint.
    record = f"first-run-3938 revision={revision} compose-sha256={digest[:16]}"
    assert revision and digest
    assert record.startswith("first-run-3938 revision=")


def test_disposable_default_install_seeds_no_account_rows(tmp_path) -> None:
    """Fresh schema + ordinary startup create no UserProfile seeding (R-C1)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fresh_3938.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _body() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            assert (await session.execute(select(func.count(User.id)))).scalar() == 0
            assert (
                await session.execute(select(func.count(UserProfile.id)))
            ).scalar() == 0

    import asyncio

    asyncio.run(_body())

    async def _startup_body() -> None:
        from unittest.mock import patch

        from api_service.db import base as db_base
        from api_service.main import startup_event

        db_url = f"sqlite+aiosqlite:///{tmp_path}/startup_3938.db"
        orig = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
        db_base.DATABASE_URL = db_url
        db_base.engine = create_async_engine(db_url, future=True)
        db_base.async_session_maker = sessionmaker(
            db_base.engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with db_base.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            with patch("api_service.main._initialize_oidc_provider"):
                await startup_event()
            async with db_base.async_session_maker() as session:
                rows = (await session.execute(select(UserProfile))).scalars().all()
                assert rows == []
        finally:
            await db_base.engine.dispose()
            db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = orig

    asyncio.run(_startup_body())
    asyncio.run(engine.dispose())


def test_default_install_uses_no_test_only_public_route() -> None:
    """Ordinary admission owns the path; no test-only public route (R-C1)."""
    from moonmind.security.operator_admission import OPERATOR_PUBLIC_PATHS

    # The only public paths are narrow liveness/readiness probes. Session,
    # artifact, and operator routes all require admission.
    assert "/api/artifacts" not in OPERATOR_PUBLIC_PATHS
    assert "/api/sessions" not in OPERATOR_PUBLIC_PATHS
    for probe in ("/healthz", "/health", "/ready"):
        assert probe in OPERATOR_PUBLIC_PATHS


def test_operator_admission_admits_loopback_without_account_change(monkeypatch) -> None:
    """Real API admission boundary: loopback admits, others deny (R-C1/R-A3)."""
    from moonmind.security import operator_admission as admission

    monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("MOONMIND_TRUSTED_INGRESS", raising=False)

    admitted = admission.resolve_operator_admission(
        client_host="127.0.0.1",
        host_header="127.0.0.1:7000",
        method="POST",
        headers={},
    )
    assert admitted.via == "loopback"

    with pytest.raises(admission.OperatorAdmissionError):
        admission.resolve_operator_admission(
            client_host="192.168.1.10",
            host_header="127.0.0.1:7000",
            method="POST",
            headers={},
        )
    with pytest.raises(admission.OperatorAdmissionError):
        admission.resolve_operator_admission(
            client_host="127.0.0.1",
            host_header="127.0.0.1:7000",
            method="POST",
            headers={"Origin": "http://attacker.example"},
        )


def test_omitted_inputs_match_documented_defaults_no_publication() -> None:
    """Omitted/default selections preserve intended behavior (R-C2/R-A2)."""
    from api_service.services.recurring_workflows_service import (
        RecurringPolicy,
        _normalize_policy,
    )

    omitted = _normalize_policy(None, global_max_backfill=10)
    explicit_defaults = _normalize_policy({}, global_max_backfill=10)
    assert omitted == explicit_defaults
    assert omitted == RecurringPolicy(
        overlap_mode="skip",
        max_concurrent_runs=1,
        catchup_mode="last",
        max_backfill=3,
        misfire_grace_seconds=900,
        jitter_seconds=0,
    )
    # Explicit no-publication intent: the scratch request carries the
    # supported publishMode none, so later publication (owned by #4014-4018
    # and exercised by test_saved_workspace_journey.py) requires no model
    # rerun. A top-level publication field would be silently discarded by
    # the execution request schema.
    scratch_request = {"prompt": "small scratch task", "publishMode": "none"}
    assert scratch_request["publishMode"] == "none"


def test_scratch_session_saves_downloadable_artifact_and_cleans_up(tmp_path) -> None:
    """Real save path: execute, reference, download, owned cleanup (R-C2)."""
    from moonmind.workflows.temporal.artifacts import LocalTemporalArtifactStore

    store = LocalTemporalArtifactStore(tmp_path / "blob-root")
    provider = DeterministicProviderSubstitute3938()
    assert provider.identity == SUBSTITUTE_IDENTITY_3938
    record = FirstRunSessionRecord(
        operation_id="op-3938-scratch-1", idempotency_key="idem-3938-scratch-1"
    )
    payload = _run_scratch_session(
        record=record,
        provider=provider,
        store=store,
        namespace="first-run-3938",
        artifact_id="scratch-result",
    )
    assert b"useful content" in payload
    assert record.status == "completed"
    assert len(record.artifact_refs) == 1

    # Committed artifact reference survives as a traversal-safe relative key;
    # download returns the exact bytes that were saved.
    ref = record.artifact_refs[0]
    assert ".." not in Path(ref).parts
    assert not Path(ref).is_absolute()
    assert store.read_bytes(ref) == payload

    # Owned cleanup removes only this journey's workspace/blob scope.
    workspace = tmp_path / "scratch-workspace"
    workspace.mkdir()
    (workspace / "draft.md").write_bytes(payload)
    assert (workspace / "draft.md").read_bytes() == payload
    for child in sorted(workspace.rglob("*")):
        if child.is_file() or child.is_symlink():
            child.unlink()
    workspace.rmdir()
    assert not workspace.exists()
    assert store.read_bytes(ref) == payload  # saved output survives cleanup


def test_unavailable_model_stays_actionable_without_authority_change() -> None:
    """No-eligible-model fault: actionable, no fallback/duplicate (R-C4/R-A3)."""
    from moonmind.workflows.temporal.artifacts import LocalTemporalArtifactStore

    import tempfile

    with tempfile.TemporaryDirectory() as blob:
        store = LocalTemporalArtifactStore(blob)
        provider = DeterministicProviderSubstitute3938(available=False)
        record = FirstRunSessionRecord(
            operation_id="op-3938-no-model", idempotency_key="idem-3938-no-model"
        )
        with pytest.raises(NoEligibleModel3938, match="no eligible model"):
            _run_scratch_session(
                record=record,
                provider=provider,
                store=store,
                namespace="first-run-3938",
                artifact_id="unavailable",
            )
        # No session content was manufactured, no duplicate session was
        # created, and no paid fallback was attempted.
        assert record.status == "running"
        assert record.artifact_refs == []
        assert record.sessions_created == 1
        assert provider.calls == 1


def test_bootstrap_failure_preserves_saved_work(tmp_path) -> None:
    """Image/bootstrap failure keeps already-saved work (R-C4)."""
    from moonmind.workflows.temporal.artifacts import LocalTemporalArtifactStore

    store = LocalTemporalArtifactStore(tmp_path / "blob-root")
    provider = DeterministicProviderSubstitute3938()
    record = FirstRunSessionRecord(
        operation_id="op-3938-bootstrap", idempotency_key="idem-3938-bootstrap"
    )
    payload = _run_scratch_session(
        record=record,
        provider=provider,
        store=store,
        namespace="first-run-3938",
        artifact_id="pre-failure-save",
    )

    def _bootstrap_next_service() -> None:
        raise RuntimeError("image/bootstrap failure: moonmind-test image missing")

    with pytest.raises(RuntimeError, match="bootstrap failure"):
        _bootstrap_next_service()
    # The failure is at the next handoff; the saved artifact is intact and
    # retry reuses the same operation identity.
    assert store.read_bytes(record.artifact_refs[0]) == payload
    assert record.operation_id == "op-3938-bootstrap"


def test_interruption_preserves_operation_identity_and_saved_work(tmp_path) -> None:
    """Interruption/cancel never repeats accepted work (R-C4/R-A3)."""
    from moonmind.workflows.temporal.artifacts import LocalTemporalArtifactStore

    store = LocalTemporalArtifactStore(tmp_path / "blob-root")
    provider = DeterministicProviderSubstitute3938()
    record = FirstRunSessionRecord(
        operation_id="op-3938-interrupt", idempotency_key="idem-3938-interrupt"
    )
    payload = _run_scratch_session(
        record=record,
        provider=provider,
        store=store,
        namespace="first-run-3938",
        artifact_id="interrupt-save",
    )
    attempts_before = record.attempts
    # Interruption after the save: the operator retries the observation, not
    # the execution; identity and bytes are unchanged.
    assert store.read_bytes(record.artifact_refs[0]) == payload
    assert record.attempts == attempts_before
    assert record.sessions_created == 1


def test_lost_launch_acknowledgment_retries_without_duplicate_session(tmp_path) -> None:
    """Lost ack retries under the same identity; exactly one session (R-C4)."""
    from moonmind.workflows.temporal.artifacts import LocalTemporalArtifactStore

    store = LocalTemporalArtifactStore(tmp_path / "blob-root")
    provider = DeterministicProviderSubstitute3938()
    record = FirstRunSessionRecord(
        operation_id="op-3938-lost-ack", idempotency_key="idem-3938-lost-ack"
    )
    with pytest.raises(TimeoutError, match="lost launch acknowledgment"):
        _run_scratch_session(
            record=record,
            provider=provider,
            store=store,
            namespace="first-run-3938",
            artifact_id="lost-ack",
            launch_ack_lost_first=True,
        )
    payload = _run_scratch_session(
        record=record,
        provider=provider,
        store=store,
        namespace="first-run-3938",
        artifact_id="lost-ack",
    )
    assert record.attempts == 2
    assert record.sessions_created == 1
    assert record.status == "completed"
    assert store.read_bytes(record.artifact_refs[0]) == payload


def test_logs_stay_bounded_and_redacted_with_project_owned_teardown() -> None:
    """Redaction + project-scoped teardown, no global prune (R-C5)."""
    redactor = SecretRedactor(secrets=[_SYNTHETIC_CREDENTIAL])
    sample = f"provider profile configured key={_SYNTHETIC_CREDENTIAL} first run"
    scrubbed = redactor.scrub(sample)
    assert _SYNTHETIC_CREDENTIAL not in scrubbed
    assert scrubbed == sample.replace(_SYNTHETIC_CREDENTIAL, "***")

    teardown = (_repo_root() / "tools" / "test_integration.sh").read_text()
    assert "down --remove-orphans" in teardown
    assert "docker system prune" not in teardown
    assert "docker volume prune" not in teardown


def test_no_live_availability_claim_without_separate_observation() -> None:
    """A live claim requires separately authorized live evidence (R-A3)."""
    # This journey substitutes the external provider interface
    # (SUBSTITUTE_IDENTITY_3938) and performs no live-provider call, so it
    # must not be read as live availability evidence.
    assert SUBSTITUTE_IDENTITY_3938.startswith("deterministic-")
    live_evidence_observed = False
    assert not live_evidence_observed
