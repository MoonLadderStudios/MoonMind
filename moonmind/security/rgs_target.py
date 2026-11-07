"""RGS observations over the shared scoped target client and bounded jobs.

The plan maps declared operations to target requests and read-only snapshots.
Before an effect may be sent, its attempt is atomically captured as uncertain.
Recovery consumes that checkpoint and never resends an attempted operation.
Unknown effects need target receipts/reconciliation; they are not retried by
this adapter. Explicit duplicate messages in the scenario remain test inputs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from moonmind.security.assessments import Contract, Text, content_key
from moonmind.security.authorization_matrix import AccessObservation
from moonmind.security.rgs_assessment import (
    MAX_INPUT_BYTES,
    Ident,
    Operation,
    Receipt,
    RgsScenarioSpec,
    RgsTraceBundle,
    Trace,
    _digest,
    assess_rgs,
)
from moonmind.security.target_assessments import (
    MAX_MESSAGE_BYTES,
    ScopedTargetClient,
    TargetRequest,
    TargetScope,
    TargetStopped,
)
from moonmind.security.workload_profiles import (
    TARGET_SCOPE_ENV,
    WORKLOAD_CREDENTIALS_TARGET,
)


class OperationRequest(Contract):
    scenario: Ident
    operation: Ident
    request: TargetRequest


class SnapshotRequest(Contract):
    scenario: Ident
    request: TargetRequest

    @model_validator(mode="after")
    def _read_only(self):
        if self.request.protocol != "http" or self.request.method != "GET":
            raise ValueError("reconciliation snapshots require a read-only HTTP GET")
        return self


class RgsTargetPlan(Contract):
    schema_version: Literal["rgs-target-plan/v1"] = "rgs-target-plan/v1"
    source_ref: Text
    operations: list[OperationRequest] = Field(min_length=1, max_length=4096)
    snapshots: list[SnapshotRequest] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _unique(self):
        ids = [(r.scenario, r.operation) for r in self.operations]
        if len(set(ids)) != len(ids) or len(
            {s.scenario for s in self.snapshots}
        ) != len(self.snapshots):
            raise ValueError("target plan operations and snapshots must be unique")
        requests = [r.request.id for r in [*self.operations, *self.snapshots]]
        if len(requests) != len(set(requests)):
            raise ValueError("target request IDs must be unique")
        return self


def _admit_plan(spec, plan, client):
    if spec.authorization_matrix is None:
        raise ValueError("target observation requires a declared authorization matrix")
    operations = {(s.id, o.id): o for s in spec.scenarios for o in s.operations}
    if set(operations) != {(r.scenario, r.operation) for r in plan.operations} or {
        s.id for s in spec.scenarios
    } != {r.scenario for r in plan.snapshots}:
        raise ValueError("target plan differs from the declared scenario scope")
    if [(s.id, o.id) for s in spec.scenarios for o in s.operations] != [
        (r.scenario, r.operation) for r in plan.operations
    ]:
        raise ValueError("target requests must preserve the declared processing order")
    for row in plan.operations:
        client.admit(row.request)
        operation = operations[row.scenario, row.operation]
        case = spec.authorization_matrix.case(operation.access_case_ref)
        identity = spec.authorization_matrix.identity(case.identity_ref)
        payload = row.request.payload
        if row.request.credential_ref != identity.credential_ref or any(
            payload.get(key) != value
            for key, value in {
                "accountRef": case.account_ref,
                "sessionRef": case.session_ref,
                "integrationRef": case.integration_ref,
                "action": case.action,
            }.items()
        ):
            raise ValueError("target request contradicts its identity or access case")
        if Operation.model_validate(payload.get("operation")) != operation:
            raise ValueError("target request changes the declared financial operation")
    for row in plan.snapshots:
        client.admit(row.request)


def _save(path: Path, bundle: RgsTraceBundle):
    body = bundle.model_dump_json(by_alias=True).encode()
    if len(body) > MAX_INPUT_BYTES:
        raise ValueError("checkpoint exceeds input bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".partial")
    with staged.open("wb") as output:
        output.write(body)
        output.flush()
        os.fsync(output.fileno())
    os.replace(staged, path)


async def observe_rgs(
    spec: RgsScenarioSpec,
    plan: RgsTargetPlan,
    client: ScopedTargetClient,
    *,
    checkpoint_path: Path,
    on_progress=None,
) -> RgsTraceBundle:
    _admit_plan(spec, plan, client)
    binding = {
        "producer": "moonmind.security.rgs_target",
        "specDigest": _digest(spec),
        "planDigest": "sha256:"
        + content_key(plan.model_dump(mode="json", by_alias=True)),
        "scopeDigest": "sha256:"
        + content_key(client.scope.model_dump(mode="json", by_alias=True)),
        "maxSeconds": f"{client.max_seconds:g}",
    }
    resuming = checkpoint_path.exists()
    if resuming:
        with checkpoint_path.open("rb") as source:
            body = source.read(MAX_INPUT_BYTES + 1)
        if len(body) > MAX_INPUT_BYTES:
            raise ValueError("checkpoint exceeds input bound")
        bundle = RgsTraceBundle.model_validate_json(body)
        if any((bundle.collection or {}).get(k) != v for k, v in binding.items()):
            raise ValueError(
                "checkpoint belongs to another specification, plan or admitted scope"
            )
        if (
            bundle.collection.get("status") == "complete"
            or bundle.collection.get("stopReason") == "prior_effects_without_checkpoint"
        ):
            return bundle
        client.requests_sent = int(bundle.collection["requestsSpent"])
        client.started = time.monotonic() - (
            time.time() - float(bundle.collection["startedAt"])
        )
    else:
        bundle = RgsTraceBundle(
            source_ref=plan.source_ref,
            collection={**binding, "startedAt": str(time.time())},
            traces=[
                Trace(
                    scenario=s.id,
                    evidence_refs=[plan.source_ref],
                    operations=[],
                    wallet_effects=[],
                    audit_effects=[],
                    balances={},
                    wager_outcomes={},
                    round_state="unobserved",
                    complete=False,
                    access_observations=[],
                )
                for s in spec.scenarios
            ],
        )
    by_scenario = {t.scenario: t for t in bundle.traces}
    scenarios = {s.id: s for s in spec.scenarios}

    def save(status="partial", reason=None, reserved=0):
        bundle.collection.update(
            requestsSpent=str(client.requests_sent + reserved),
            secondsSpent=str(time.monotonic() - client.started),
            status=status,
        )
        if reason:
            bundle.collection["stopReason"] = reason
        else:
            bundle.collection.pop("stopReason", None)
        _save(checkpoint_path, bundle)
        if on_progress:
            on_progress(bundle)

    def preflight(request):
        cost = client.preflight(request)
        # Leave room for a full response, its validated reconciliation trace
        # and checkpoint metadata before handing another request to transport.
        if (
            len(bundle.model_dump_json(by_alias=True).encode()) + 3 * MAX_MESSAGE_BYTES
            > MAX_INPUT_BYTES
        ):
            raise TargetStopped("capture_budget")
        return cost

    save()
    try:
        prior_effects = False
        for row in plan.snapshots:
            previous_trace = by_scenario[row.scenario]
            save(reserved=preflight(row.request))
            observed = await client.exchange(row.request)
            snapshot = Trace.model_validate(observed.payload)
            if snapshot.scenario != row.scenario:
                raise TargetStopped("snapshot_outside_scenario")
            bundle.observations = [*(bundle.observations or []), observed]
            recorded = {r.operation: r for r in previous_trace.operations}
            accesses = {
                a.operation: a for a in previous_trace.access_observations or []
            }
            operations = {o.id: o for o in scenarios[row.scenario].operations}
            existing = {r.operation for r in snapshot.operations}
            transactions = {e.transaction_id for e in snapshot.wallet_effects}
            prior_effects |= bool(existing or transactions)
            for receipt in snapshot.operations:
                if receipt.operation not in operations:
                    raise TargetStopped("snapshot_outside_operation_scope")
                if (
                    receipt.operation not in recorded
                    or recorded[receipt.operation].decision == "unknown"
                ):
                    recorded[receipt.operation] = receipt
                if receipt.operation not in accesses:
                    accesses[receipt.operation] = AccessObservation(
                        operation=receipt.operation,
                        case_ref=operations[receipt.operation].access_case_ref,
                        observed="unknown",
                        evidence_refs=[observed.evidence_ref],
                    )
            snapshot.operations = list(recorded.values())
            snapshot.access_observations = list(accesses.values())
            complete = snapshot.complete
            snapshot.complete = False
            snapshot.evidence_refs = [plan.source_ref, observed.evidence_ref]
            by_scenario[row.scenario] = snapshot
            bundle.traces = [by_scenario[s.id] for s in spec.scenarios]
            save()
            if not complete:
                raise TargetStopped("initial_reconciliation_incomplete")
        if prior_effects and not resuming:
            # The target is independent of the lost output mount. Retain its
            # confirmed effects, but never guess the predecessor's spent budget
            # or replay operations when no admitted checkpoint is available.
            raise TargetStopped("prior_effects_without_checkpoint")
        for row in plan.operations:
            trace = by_scenario[row.scenario]
            if any(r.operation == row.operation for r in trace.operations):
                continue
            # Check the unchanged total budget before recording an attempt.
            cost = preflight(row.request)
            operation = next(
                o for o in scenarios[row.scenario].operations if o.id == row.operation
            )
            receipt = Receipt(operation=row.operation, decision="unknown")
            access = AccessObservation(
                operation=row.operation,
                case_ref=operation.access_case_ref,
                observed="unknown",
                evidence_refs=[plan.source_ref],
            )
            trace.operations.append(receipt)
            trace.access_observations.append(access)
            # The durable, collected capture precedes the side-effect handoff.
            save(reserved=cost)
            observed = await client.exchange(row.request)
            bundle.observations = [*(bundle.observations or []), observed]
            if observed.payload.get("decision") not in {
                "accepted",
                "denied",
                "unknown",
            }:
                raise TargetStopped("invalid_operation_receipt")
            receipt.decision = observed.payload["decision"]
            observed_access = (
                "deny"
                if observed.status in {401, 403}
                else observed.payload.get("access", "unknown")
            )
            if observed_access not in {"allow", "deny", "unknown"}:
                raise TargetStopped("invalid_access_receipt")
            access.observed = observed_access
            access.evidence_refs = [observed.evidence_ref]
            save()
        for row in plan.snapshots:
            trace = by_scenario[row.scenario]
            if trace.complete:
                continue
            save(reserved=preflight(row.request))
            observed = await client.exchange(row.request)
            bundle.observations = [*(bundle.observations or []), observed]
            snapshot = Trace.model_validate(observed.payload)
            if snapshot.scenario != row.scenario:
                raise TargetStopped("snapshot_outside_scenario")
            # Only the target's actual persisted receipt can resolve an
            # uncertain operation. Missing receipts remain unknown; no send.
            recorded = {r.operation: r for r in snapshot.operations}
            for receipt in trace.operations:
                if receipt.decision == "unknown" and receipt.operation in recorded:
                    receipt.decision = recorded[receipt.operation].decision
            snapshot.operations = trace.operations
            snapshot.access_observations = trace.access_observations
            snapshot.evidence_refs = list(
                dict.fromkeys([*trace.evidence_refs, observed.evidence_ref])
            )[:64]
            by_scenario[row.scenario] = snapshot
            bundle.traces = [by_scenario[s.id] for s in spec.scenarios]
            save()
        save("complete")
    except (TargetStopped, PermissionError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, TargetStopped) else type(exc).__name__
        save(reason=reason)
    return bundle


def main(argv=None, *, credential_root=Path(WORKLOAD_CREDENTIALS_TARGET)):
    parser = argparse.ArgumentParser(prog="python -m moonmind.security.rgs_target")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--resume")
    parser.add_argument("--capture", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-seconds", type=float, default=300)
    args = parser.parse_args(argv)
    stopping = []
    previous = {
        n: signal.signal(n, lambda *_: stopping.append(True))
        for n in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        bodies = {}
        for key in ("spec", "plan"):
            with open(getattr(args, key), "rb") as source:
                bodies[key] = source.read(MAX_INPUT_BYTES + 1)
            if len(bodies[key]) > MAX_INPUT_BYTES:
                raise ValueError("input bound exceeded")
        spec = RgsScenarioSpec.model_validate_json(bodies["spec"])
        plan = RgsTargetPlan.model_validate_json(bodies["plan"])
        scope = TargetScope.model_validate_json(os.environ[TARGET_SCOPE_ENV])
        if os.environ.get("MOONMIND_EGRESS_POLICY_REF") != scope.web.egress_policy_ref:
            raise PermissionError(
                "target scope differs from the admitted egress policy"
            )
        if not 0 < args.max_seconds <= scope.web.timeout_seconds:
            raise ValueError("target time budget exceeds admission")
        capture = Path(args.capture)
        if args.resume and not capture.exists():
            with open(args.resume, "rb") as source:
                resume = source.read(MAX_INPUT_BYTES + 1)
            if len(resume) > MAX_INPUT_BYTES:
                raise ValueError("checkpoint exceeds input bound")
            _save(capture, RgsTraceBundle.model_validate_json(resume))

        def progress(bundle):
            report = assess_rgs(
                spec,
                bundle,
                spec_input_ref=args.spec,
                traces_input_ref=args.capture,
                input_digests={
                    "spec": "sha256:" + hashlib.sha256(bodies["spec"]).hexdigest(),
                    "traces": "sha256:"
                    + hashlib.sha256(capture.read_bytes()).hexdigest(),
                },
            )
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            staged = path.with_name(path.name + ".partial")
            staged.write_text(report.model_dump_json(by_alias=True))
            os.replace(staged, path)

        client = ScopedTargetClient(
            scope,
            credential_root=credential_root,
            proxy=os.environ.get("MOONMIND_EGRESS_PROXY"),
            should_stop=lambda: bool(stopping),
            max_seconds=args.max_seconds,
        )
        bundle = asyncio.run(
            observe_rgs(
                spec, plan, client, checkpoint_path=capture, on_progress=progress
            )
        )
        progress(bundle)
        print("RGS target observation " + bundle.collection["status"], flush=True)
        return 0
    except (OSError, KeyError, ValueError, PermissionError) as exc:
        location = traceback.extract_tb(exc.__traceback__)[-1]
        print(
            f"RGS target observation refused: {type(exc).__name__} at {Path(location.filename).name}:{location.lineno}",
            file=sys.stderr,
        )
        return 2
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    sys.exit(main())
