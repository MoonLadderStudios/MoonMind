"""Bounded application observations with durable, non-replayable mutation attempts.

Only assertion projections and state digests cross the response boundary. A
checkpoint reserves each request before transport, including verification probes.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from moonmind.security.application_access import (
    ApplicationAccessBundle,
    ApplicationAccessObservation,
    ApplicationAccessSpec,
    Digest,
    StateValue,
    assess_application_access,
    compare_state,
    project_response,
    project_state_values,
    spec_digest,
)
from moonmind.security.assessments import Contract, content_key
from moonmind.security.authorization_matrix import IdentityRef
from moonmind.security.realtime_scope import TransportBudget
from moonmind.security.target_assessments import (
    ScopedTargetClient,
    TargetScope,
    TargetStopped,
)
from moonmind.security.workload_profiles import (
    TARGET_SCOPE_ENV,
    WORKLOAD_CREDENTIALS_TARGET,
)

MAX_INPUT_BYTES = 8 * 1024 * 1024


class CaseProgress(Contract):
    case_ref: IdentityRef
    phase: Literal["pending", "attempted", "responded", "complete"] = "pending"
    state_before: list[StateValue] = Field(default_factory=list, max_length=32)


class ApplicationAccessCapture(Contract):
    schema_version: Literal["application-access-capture/v1"] = (
        "application-access-capture/v1"
    )
    producer: Literal["moonmind.security.application_access_target"] = (
        "moonmind.security.application_access_target"
    )
    scope: TargetScope
    scope_digest: Digest
    max_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)
    started_at: float = Field(gt=0, allow_inf_nan=False)
    next_request_at: float = Field(gt=0, allow_inf_nan=False)
    requests_spent: int = Field(0, ge=0, le=8192)
    transport_budget: TransportBudget | None = None
    bundle: ApplicationAccessBundle
    progress: list[CaseProgress] = Field(min_length=1, max_length=512)
    status: Literal["partial", "complete"] = "partial"
    stop_reason: (
        Literal[
            "cancelled",
            "timeout",
            "budget_exhausted",
            "request_failed",
            "malformed_output",
            "effect_uncertain",
            "verification_unavailable",
            "unsupported",
        ]
        | None
    ) = None

    @model_serializer(mode="wrap")
    def _legacy_shape(self, handler):
        result = handler(self)
        if self.transport_budget is None:
            result.pop("transportBudget", None)
            result.pop("transport_budget", None)
        return result

    @model_validator(mode="after")
    def _consistent(self):
        if (self.scope.realtime is None) != (self.transport_budget is None):
            raise ValueError("real-time captures require durable transport counters")
        if self.transport_budget is not None and any(
            getattr(self.transport_budget, name)
            > getattr(self.scope.realtime, "max_" + name)
            for name in ("connections", "messages", "bytes")
        ):
            raise ValueError("capture transport budget exceeds scope")
        if self.scope_digest != content_key(self.scope.model_dump(mode="json")):
            raise ValueError("capture scope digest differs")
        progress = [p.case_ref for p in self.progress]
        observations = [o.case_ref for o in self.bundle.observations]
        if len(set(progress)) != len(progress) or progress != observations:
            raise ValueError("capture progress differs from observations")
        if self.status == "complete" and (
            self.stop_reason is not None
            or any(p.phase != "complete" for p in self.progress)
        ):
            raise ValueError("complete capture has unfinished requests")
        if (
            self.requests_spent > self.scope.max_requests
            or self.max_seconds > self.scope.web.timeout_seconds
        ):
            raise ValueError("capture budget exceeds scope")
        return self


def validate_capture_spec(
    spec: ApplicationAccessSpec, capture: ApplicationAccessCapture
) -> None:
    """Verify retained scope and canonical case binding without doing any I/O."""
    # Models are mutable. Revalidate nested contracts at this trust boundary,
    # including request payload correlation and scope/budget consistency.
    canonical = ApplicationAccessSpec.model_validate(spec.model_dump(mode="json"))
    retained = ApplicationAccessCapture.model_validate(capture.model_dump(mode="json"))
    cases = [case.case_ref for case in canonical.cases]
    if (
        retained.bundle.spec_digest != spec_digest(canonical)
        or [p.case_ref for p in retained.progress] != cases
        or [o.case_ref for o in retained.bundle.observations] != cases
    ):
        raise ValueError("capture differs from declared cases")
    client = ScopedTargetClient(scope=retained.scope, proxy="admission-only")
    for case, observation in zip(canonical.cases, retained.bundle.observations):
        if observation.transcript is not None and (
            case.request.realtime_case_ref is None
            or any(
                event.request_id != case.request.id
                or event.correlation_id != case.request.id
                or event.case_ref != case.request.realtime_case_ref
                for event in observation.transcript
            )
        ):
            raise ValueError("transcript differs from the declared operation")
        for request in (case.request, case.identity_check, case.state_check):
            if request is not None:
                client.admit(request)


def _save(path, model):
    body = model.model_dump_json(by_alias=True).encode()
    if len(body) > MAX_INPUT_BYTES:
        raise ValueError("capture exceeds bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".partial")
    with staged.open("wb") as output:
        output.write(body)
        output.flush()
        os.fsync(output.fileno())
    os.replace(staged, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _read(path):
    with Path(path).open("rb") as source:
        body = source.read(MAX_INPUT_BYTES + 1)
    if len(body) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds bound")
    return body


async def observe_application_access(
    spec, client, *, checkpoint_path, on_progress=None
):
    # Admit the entire plan before opening credentials or sending even a probe.
    for case in spec.cases:
        for request in (case.request, case.identity_check, case.state_check):
            if request is not None:
                client.admit(request)
    checkpoint_path = Path(checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint_path.with_name(checkpoint_path.name + ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("capture already in use") from None
        return await _observe(spec, client, checkpoint_path, on_progress)


async def _observe(spec, client, path, on_progress):
    scope_digest = content_key(client.scope.model_dump(mode="json"))
    if path.exists():
        capture = ApplicationAccessCapture.model_validate_json(_read(path))
        validate_capture_spec(spec, capture)
        if (
            capture.bundle.spec_digest != spec_digest(spec)
            or capture.scope_digest != scope_digest
            or capture.max_seconds != client.max_seconds
            or [p.case_ref for p in capture.progress]
            != [c.case_ref for c in spec.cases]
            or [o.case_ref for o in capture.bundle.observations]
            != [c.case_ref for c in spec.cases]
        ):
            raise ValueError("capture differs from admitted specification or budget")
        if capture.status == "complete" or capture.stop_reason in {
            "cancelled",
            "timeout",
            "budget_exhausted",
        }:
            return capture
        client.requests_sent = capture.requests_spent
        if capture.transport_budget is not None:
            client.restore_transport(capture.transport_budget)
        client.started = time.monotonic() - max(0, time.time() - capture.started_at)
        client.next_request = time.monotonic() + max(
            0, capture.next_request_at - time.time()
        )
    else:
        capture = ApplicationAccessCapture(
            scope=client.scope,
            scope_digest=scope_digest,
            max_seconds=client.max_seconds,
            started_at=time.time() - (time.monotonic() - client.started),
            next_request_at=time.time(),
            transport_budget=client.transport_budget if client.scope.realtime else None,
            bundle=ApplicationAccessBundle(
                spec_digest=spec_digest(spec),
                observations=[
                    ApplicationAccessObservation(
                        case_ref=c.case_ref, evidence_refs=[spec.source_ref]
                    )
                    for c in spec.cases
                ],
            ),
            progress=[CaseProgress(case_ref=c.case_ref) for c in spec.cases],
        )

    validate_capture_spec(spec, capture)

    def save():
        _save(path, capture)
        if on_progress:
            on_progress(capture)

    async def exchange(request, observation):
        cost = client.preflight(request)
        capture.requests_spent = client.requests_sent + cost
        reservation = client.reserve_transport(request)
        if capture.transport_budget is not None:
            capture.transport_budget = reservation
        # Reserve the rate slot as well: a successor cannot burst after a crash.
        capture.next_request_at = (
            max(time.time(), capture.next_request_at)
            + 1 / client.scope.web.max_requests_per_second
        )
        save()
        try:
            result = await client.exchange(request)
        except BaseException:
            # A crash cannot prove which frames were sent. Preserve the durable
            # worst-case reservation even during later reconciliation probes.
            if capture.transport_budget is not None:
                client.restore_transport(reservation)
            raise
        else:
            if capture.transport_budget is not None:
                capture.transport_budget = client.transport_budget
        finally:
            if request.realtime_case_ref is not None:
                observation.transcript = client.transport_transcript
        if result.evidence_ref not in observation.evidence_refs:
            observation.evidence_refs = [
                *observation.evidence_refs[-62:],
                result.evidence_ref,
            ]
        return result

    capture.stop_reason = None
    save()
    active = None
    try:
        for case, progress, observation in zip(
            spec.cases, capture.progress, capture.bundle.observations
        ):
            if progress.phase == "complete":
                continue
            active = observation
            observation.stop_reason = None
            identity = await exchange(case.identity_check, observation)
            identity_results = project_response(
                identity.payload, case.identity_assertions
            )
            if not 200 <= identity.status < 300 or not all(
                r.matched is True for r in identity_results
            ):
                observation.identity_after = identity_results
                observation.stop_reason = "identity_failed"
                progress.phase = "complete"
                save()
                continue
            if progress.phase == "pending":
                observation.identity_before = identity_results
                if case.state_check:
                    state = await exchange(case.state_check, observation)
                    if not 200 <= state.status < 300:
                        raise TargetStopped("verification_unavailable")
                    progress.state_before = project_state_values(
                        state.payload, case.state_assertions
                    )
                    if (
                        case.receipt_assertion
                        and project_response(state.payload, [case.receipt_assertion])[
                            0
                        ].matched
                    ):
                        # Existing receipt without our before-state/attempt is not a
                        # license to replay, nor evidence that this case passed.
                        observation.receipt = project_response(
                            state.payload, [case.receipt_assertion]
                        )[0]
                        observation.receipt_ref = case.mutation_ref
                        observation.stop_reason = "effect_uncertain"
                        progress.phase = "complete"
                        save()
                        continue
                    if not all(v.present for v in progress.state_before):
                        observation.stop_reason = "verification_unavailable"
                        progress.phase = "complete"
                        save()
                        continue
                observation.attempt_ref = "attempt-" + uuid.uuid4().hex
                progress.phase = "attempted"
                save()  # Durable intent precedes both reservation and transport.
                response = await exchange(case.request, observation)
                observation.status = response.status
                observation.response = project_response(
                    response.payload,
                    case.allow_assertions
                    + case.deny_assertions
                    + case.protected_assertions,
                )
                progress.phase = "responded"
                save()
            if case.state_check:
                state = await exchange(case.state_check, observation)
                if not 200 <= state.status < 300:
                    raise TargetStopped("verification_unavailable")
                observation.state = compare_state(
                    progress.state_before,
                    project_state_values(state.payload, case.state_assertions),
                    case.state_assertions,
                )
                if case.receipt_assertion:
                    observation.receipt = project_response(
                        state.payload, [case.receipt_assertion]
                    )[0]
                    if observation.receipt.matched:
                        observation.receipt_ref = case.mutation_ref
            identity = await exchange(case.identity_check, observation)
            observation.identity_after = project_response(
                identity.payload if 200 <= identity.status < 300 else None,
                case.identity_assertions,
            )
            if not 200 <= identity.status < 300 or not all(
                r.matched is True for r in observation.identity_after
            ):
                observation.stop_reason = "identity_failed"
            if progress.phase == "attempted" and observation.stop_reason is None:
                # Receipt may establish an effect, but cannot reconstruct a lost
                # response or invent its response predicates.
                observation.stop_reason = "effect_uncertain"
            progress.phase = "complete"
            save()
        capture.status = "complete"
    except (TargetStopped, PermissionError, asyncio.CancelledError) as exc:
        reason = (
            "cancelled"
            if isinstance(exc, asyncio.CancelledError)
            else {
                "verification_unavailable": "verification_unavailable",
                "cancelled": "cancelled",
                "time_budget": "timeout",
                "request_budget": "budget_exhausted",
                "invalid_target_response": "malformed_output",
                "unsafe_target_response": "malformed_output",
            }.get(
                str(exc),
                (
                    "unsupported"
                    if str(exc).startswith("unsupported_")
                    else (
                        "budget_exhausted"
                        if str(exc).startswith("transport_") and "budget" in str(exc)
                        else "request_failed"
                    )
                ),
            )
        )
        capture.stop_reason = reason
        if active is not None:
            active.stop_reason = reason
    finally:
        save()
    return capture


def main(argv=None, *, credential_root=Path(WORKLOAD_CREDENTIALS_TARGET)):
    parser = argparse.ArgumentParser()
    for name in ("spec", "capture", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--resume")
    parser.add_argument("--max-seconds", type=float, default=300)
    args = parser.parse_args(argv)
    stopping = []
    previous = {
        n: signal.signal(n, lambda *_: stopping.append(True))
        for n in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        if (
            len(
                {
                    Path(args.spec).resolve(),
                    Path(args.capture).resolve(),
                    Path(args.output).resolve(),
                }
            )
            != 3
        ):
            raise ValueError("specification, capture and report require distinct files")
        body = _read(args.spec)
        spec = ApplicationAccessSpec.model_validate_json(body)
        scope = TargetScope.model_validate_json(os.environ[TARGET_SCOPE_ENV])
        if os.environ.get("MOONMIND_EGRESS_POLICY_REF") != scope.web.egress_policy_ref:
            raise PermissionError("egress policy differs")
        if not 0 < args.max_seconds <= scope.web.timeout_seconds:
            raise ValueError("time budget exceeds admission")
        path = Path(args.capture)
        if args.resume:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.with_name(path.name + ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if not path.exists():
                    _save(
                        path,
                        ApplicationAccessCapture.model_validate_json(
                            _read(args.resume)
                        ),
                    )

        def progress(capture):
            report = assess_application_access(spec, capture.bundle)
            report.input_digests = {
                "spec": hashlib.sha256(body).hexdigest(),
                "capture": hashlib.sha256(_read(path)).hexdigest(),
            }
            _save(Path(args.output), report)

        client = ScopedTargetClient(
            scope,
            credential_root=credential_root,
            proxy=os.environ.get("MOONMIND_EGRESS_PROXY"),
            should_stop=lambda: bool(stopping),
            max_seconds=args.max_seconds,
        )
        capture = asyncio.run(
            observe_application_access(
                spec, client, checkpoint_path=path, on_progress=progress
            )
        )
        progress(capture)
        print("Application access observation " + capture.status, flush=True)
        return 0
    except (OSError, KeyError, ValueError, PermissionError):
        print(
            "Application access observation refused: invalid input or scope",
            file=sys.stderr,
        )
        return 2
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    sys.exit(main())
