"""Real HTTP/WebSocket target access, persisted wallet effects and RGS evidence.

The synthetic target is an aiohttp application with a SQLite account/session,
integration, wallet and audit store. The target independently authenticates
issued tokens. No transport, authorization decision or persistence is mocked.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import secrets
import signal
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from aiohttp import web

from moonmind.config.container_backend_settings import (
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.security.workload_profiles import WORKLOAD_PROFILES_ENV_KEY
from tests.integration.security import test_rgs_assessment_jobs as rgs_jobs
from tests.unit.security.test_rgs_assessment import access_inputs

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
Job = rgs_jobs.Job
environment = rgs_jobs.environment


def retain_target_evidence(label, report, capture, target):
    output = (
        Path(__file__).resolve().parents[3] / "artifacts/rgs-target-evidence" / label
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "assessment.json").write_text(report.model_dump_json(by_alias=True))
    (output / "capture.json").write_text(capture.model_dump_json(by_alias=True))
    (output / "target-store.json").write_text(
        json.dumps(
            {
                "source": "executed SQLite queries against the synthetic target's effect owner",
                "requests": target.requests,
                "identities": target.db.execute(
                    "SELECT id,account,session,integration FROM identities ORDER BY id"
                ).fetchall(),
                "effects": target.db.execute(
                    "SELECT * FROM effects ORDER BY rowid"
                ).fetchall(),
                "audit": target.db.execute(
                    "SELECT * FROM audit ORDER BY rowid"
                ).fetchall(),
                "balances": target.db.execute(
                    "SELECT * FROM balances ORDER BY account"
                ).fetchall(),
                "receipts": target.db.execute(
                    "SELECT * FROM receipts ORDER BY rowid"
                ).fetchall(),
            },
            indent=2,
        )
        + "\n"
    )


class Target:
    def __init__(self, path, spec, *, broken=False):
        self.db = sqlite3.connect(path)
        self.db.executescript(
            """
            CREATE TABLE identities(token TEXT PRIMARY KEY, id TEXT, account TEXT, session TEXT, integration TEXT);
            CREATE TABLE effects(tx TEXT PRIMARY KEY, account TEXT, amount INTEGER);
            CREATE TABLE audit(tx TEXT PRIMARY KEY, account TEXT, amount INTEGER);
            CREATE TABLE receipts(id TEXT PRIMARY KEY, decision TEXT);
            CREATE TABLE balances(account TEXT PRIMARY KEY, amount INTEGER);
            CREATE TABLE delegations(integration TEXT, account TEXT, action TEXT);
        """
        )
        self.spec, self.broken = spec, broken
        self.secrets = {}
        self.requests = []
        self.times = []
        self.redirects = []
        self.hold_response = None
        for identity in spec["authorizationMatrix"]["identities"]:
            token = secrets.token_urlsafe(32)
            self.secrets[identity["credentialRef"]] = token
            self.db.execute(
                "INSERT INTO identities VALUES(?,?,?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    identity["id"],
                    identity["accountRef"],
                    identity["sessionRef"],
                    identity["integrationRef"],
                ),
            )
        for account, balance in spec["scenarios"][0]["initialBalances"].items():
            self.db.execute("INSERT INTO balances VALUES(?,?)", (account, balance))
        self.db.execute(
            "INSERT INTO delegations VALUES(?,?,?)",
            (
                spec["authorizationMatrix"]["identities"][1]["integrationRef"],
                spec["authorizationMatrix"]["identities"][0]["accountRef"],
                "cashout",
            ),
        )
        self.db.commit()

    def act(self, token, payload):
        identity = self.db.execute(
            "SELECT id,account,session,integration FROM identities WHERE token=?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
        self.requests.append(payload["id"])
        self.times.append(time.monotonic())
        if not identity:
            return {"access": "deny", "decision": "denied"}
        # A service integration may explicitly delegate account access. No
        # expectation from the assessment matrix is consulted by the target.
        own = identity[1] == payload["accountRef"]
        delegated = (
            self.db.execute(
                "SELECT 1 FROM delegations WHERE integration=? AND account=? AND action=?",
                (identity[3], payload["accountRef"], payload["action"]),
            ).fetchone()
            is not None
        )
        allowed = (
            (own or delegated)
            and identity[2] == payload["sessionRef"]
            and identity[3] == payload["integrationRef"]
        )
        if not allowed and not self.broken:
            decision = "denied"
        else:
            operation = payload["operation"]
            amount = -100 if operation["kind"] == "wager" else 150
            # The target's transaction store is the effect owner. A replay
            # returns its persisted receipt without a second wallet mutation.
            with self.db:
                added = self.db.execute(
                    "INSERT OR IGNORE INTO effects VALUES(?,?,?)",
                    (operation["transactionId"], payload["accountRef"], amount),
                ).rowcount
                if added:
                    self.db.execute(
                        "UPDATE balances SET amount=amount+? WHERE account=?",
                        (amount, payload["accountRef"]),
                    )
                    self.db.execute(
                        "INSERT INTO audit VALUES(?,?,?)",
                        (operation["transactionId"], payload["accountRef"], amount),
                    )
            decision = "accepted"
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO receipts VALUES(?,?)",
                (payload["operation"]["id"], decision),
            )
        return {
            "access": "allow" if allowed or self.broken else "deny",
            "decision": decision,
        }

    async def handle(self, request):
        if request.path == "/redirect":
            raise web.HTTPFound("/unadmitted")
        if request.path == "/unadmitted":
            self.redirects.append(request.path)
        if request.path == "/snapshot":
            return web.json_response(self.snapshot())
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if request.path == "/channel":
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            async for message in socket:
                await socket.send_json(self.act(token, json.loads(message.data)))
            return socket
        payload = await request.json()
        answer = self.act(token, payload)
        if self.hold_response is not None:
            await self.hold_response.wait()
        return web.json_response(
            answer, status=403 if answer["access"] == "deny" else 200
        )

    def snapshot(self):
        effects = [
            {
                "id": "effect-" + tx,
                "transactionId": tx,
                "kind": "debit" if amount < 0 else "credit",
                "accountRef": account,
                "amount": amount,
            }
            for tx, account, amount in self.db.execute(
                "SELECT * FROM effects ORDER BY rowid"
            )
        ]
        outcomes = {w["id"]: "unplaced" for w in self.spec["scenarios"][0]["wagers"]}
        for operation in self.spec["scenarios"][0]["operations"]:
            if self.db.execute(
                "SELECT 1 FROM effects WHERE tx=?", (operation["transactionId"],)
            ).fetchone():
                outcomes[operation["wagerRef"]] = (
                    "active" if operation["kind"] == "wager" else "cashed_out"
                )
        audit = [
            {
                "id": "effect-" + tx,
                "transactionId": tx,
                "kind": "debit" if amount < 0 else "credit",
                "accountRef": account,
                "amount": amount,
            }
            for tx, account, amount in self.db.execute(
                "SELECT * FROM audit ORDER BY rowid"
            )
        ]
        return {
            "scenario": self.spec["scenarios"][0]["id"],
            "evidenceRefs": ["fixture:sqlite-target-snapshot"],
            "operations": [
                {"operation": op, "decision": decision}
                for op, decision in self.db.execute(
                    "SELECT * FROM receipts ORDER BY rowid"
                )
            ],
            "walletEffects": effects,
            "auditEffects": audit,
            "balances": dict(self.db.execute("SELECT * FROM balances")),
            "wagerOutcomes": outcomes,
            "roundState": "finished",
            "complete": True,
        }


def target_spec():
    raw = access_inputs()
    scenario = raw["scenarios"][0]
    scenario["wagers"] = [scenario["wagers"][0]]
    first = scenario["operations"][0]
    cases = raw["authorizationMatrix"]["cases"]
    for name, changes in (
        ("cross-session", {"sessionRef": "session-b"}),
        ("cross-integration", {"integrationRef": "integration-b"}),
        (
            "delegated",
            {
                "identityRef": "other",
                "sessionRef": "session-b",
                "integrationRef": "integration-b",
                "action": "cashout",
                "expected": "allow",
            },
        ),
    ):
        cases.append({**cases[0], "id": name, "expected": "deny", **changes})
    for index, case in enumerate(cases[1:], 1):
        scenario["operations"].append(
            {
                **first,
                "id": case["id"],
                "accessCaseRef": case["id"],
                "kind": case["action"],
                "transactionId": "tx-" + case["id"],
                "receivedAt": 1 + index,
                "processedAt": 1 + index,
            }
        )
    scenario["operations"][-1].update(receivedAt=10, processedAt=10)
    scenario["operations"].insert(
        2,
        {
            **first,
            "id": "unauthorized-replay",
            "accessCaseRef": "cross-account",
            "receivedAt": 2,
            "processedAt": 2,
        },
    )
    return raw


async def start_target(tmp_path, *, broken=False):
    spec = target_spec()
    target = Target(tmp_path / "target.sqlite", spec, broken=broken)
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", target.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    scope = {
        "web": {
            "mode": "active",
            "activeStrength": "low",
            "origin": f"http://target.example.test:{port}",
            "routes": [
                {"path": "/operation", "methods": ["POST"]},
                {"path": "/channel", "methods": ["GET"]},
                {"path": "/snapshot", "methods": ["GET"]},
                {"path": "/redirect", "methods": ["GET"]},
            ],
            "startPaths": ["/snapshot"],
            "egressPolicyRef": "synthetic-target",
            "maxRequestsPerSecond": 100,
        },
        "messages": {"/channel": ["operation"]},
        "credentialSlots": {
            ref: "identity-" + str(i) for i, ref in enumerate(target.secrets)
        },
        "maxRequests": 64,
    }
    credential_root = tmp_path / "credentials"
    credential_root.mkdir()
    for ref, slot in scope["credentialSlots"].items():
        (credential_root / slot).write_text(target.secrets[ref])
    plan = {
        "schemaVersion": "rgs-target-plan/v1",
        "sourceRef": "fixture:target-plan",
        "operations": [],
        "snapshots": [],
    }
    matrix = {c["id"]: c for c in spec["authorizationMatrix"]["cases"]}
    identities = {i["id"]: i for i in spec["authorizationMatrix"]["identities"]}
    for i, op in enumerate(spec["scenarios"][0]["operations"]):
        case = matrix[op["accessCaseRef"]]
        plan["operations"].append(
            {
                "scenario": spec["scenarios"][0]["id"],
                "operation": op["id"],
                "request": {
                    "id": op["id"],
                    "protocol": "http" if i % 2 == 0 else "websocket",
                    "path": "/operation" if i % 2 == 0 else "/channel",
                    "method": "POST" if i % 2 == 0 else "GET",
                    "messageType": None if i % 2 == 0 else "operation",
                    "credentialRef": identities[case["identityRef"]]["credentialRef"],
                    "payload": {
                        "id": op["id"],
                        "type": "operation",
                        "accountRef": case["accountRef"],
                        "sessionRef": case["sessionRef"],
                        "integrationRef": case["integrationRef"],
                        "action": case["action"],
                        "operation": op,
                    },
                },
            }
        )
    plan["snapshots"].append(
        {
            "scenario": spec["scenarios"][0]["id"],
            "request": {
                "id": "snapshot",
                "path": "/snapshot",
                "method": "GET",
                "protocol": "http",
                "payload": {},
            },
        }
    )
    return (
        target,
        runner,
        spec,
        plan,
        scope,
        credential_root,
        f"http://127.0.0.1:{port}",
    )


@pytest.mark.parametrize("broken", [False, True])
async def test_real_target_matrix_and_transport_feed_rgs_assertions(tmp_path, broken):
    from moonmind.security.rgs_assessment import RgsScenarioSpec, assess_rgs
    from moonmind.security.rgs_target import RgsTargetPlan, observe_rgs
    from moonmind.security.target_assessments import ScopedTargetClient, TargetScope

    target, runner, raw, plan, scope, credentials, proxy = await start_target(
        tmp_path, broken=broken
    )
    try:
        spec = RgsScenarioSpec.model_validate(raw)
        client = ScopedTargetClient(
            TargetScope.model_validate(scope), credential_root=credentials, proxy=proxy
        )
        traces = await observe_rgs(
            spec,
            RgsTargetPlan.model_validate(plan),
            client,
            checkpoint_path=tmp_path / "capture.json",
        )
        report = assess_rgs(spec, traces)
        assert report.outcome == ("violations" if broken else "consistent")
        access = [c for c in report.checks if c.rule == "target-access"]
        assert len(access) == len(raw["scenarios"][0]["operations"])
        assert any(c.expected == "allow" for c in access)
        assert any(c.expected == "deny" for c in access)
        assert all(c.evidence_refs for c in access)
        assert any(c.decision == "violation" for c in access) == broken
        assert "transport_not_exercised" not in report.limitations
        assert "authorization_matrix_not_exercised" not in report.limitations
        assert target.requests == [o["id"] for o in raw["scenarios"][0]["operations"]]
        if not broken:
            assert target.db.execute("SELECT amount FROM balances").fetchone() == (
                1050,
            )
            assert target.db.execute("SELECT COUNT(*) FROM effects").fetchone() == (2,)
            assert target.db.execute("SELECT COUNT(*) FROM audit").fetchone() == (2,)
        assert target.redirects == []
        assert all(
            token not in report.model_dump_json() for token in target.secrets.values()
        )
        retain_target_evidence(
            "permissive" if broken else "reference", report, traces, target
        )
    finally:
        await runner.cleanup()
        target.db.close()


async def test_shared_transport_refuses_unadmitted_requests_before_network(tmp_path):
    from moonmind.security.target_assessments import (
        ScopedTargetClient,
        TargetRequest,
        TargetScope,
    )

    target, runner, _, plan, scope, credentials, proxy = await start_target(tmp_path)
    try:
        client = ScopedTargetClient(
            TargetScope.model_validate(scope), credential_root=credentials, proxy=proxy
        )
        original = plan["operations"][0]["request"]
        for changes in (
            {"path": "/unadmitted"},
            {"path": "http://other.example.test/operation"},
            {"method": "DELETE"},
            {"credentialRef": "secret://unadmitted"},
            {
                "protocol": "websocket",
                "path": "/channel",
                "method": "GET",
                "messageType": "unadmitted",
            },
        ):
            with pytest.raises((PermissionError, ValueError)):
                await client.exchange(
                    TargetRequest.model_validate({**original, **changes})
                )
        assert target.requests == []
        assert client.requests_sent == 0
        with pytest.raises(PermissionError):
            await client.exchange(
                TargetRequest(
                    id="redirect",
                    protocol="http",
                    path="/redirect",
                    method="GET",
                    payload={},
                )
            )
        assert target.redirects == []
    finally:
        await runner.cleanup()
        target.db.close()


async def test_observer_checkpoint_prevents_repeating_confirmed_effects(tmp_path):
    from moonmind.security.rgs_assessment import RgsScenarioSpec, assess_rgs
    from moonmind.security.rgs_target import RgsTargetPlan, observe_rgs
    from moonmind.security.target_assessments import ScopedTargetClient, TargetScope

    target, runner, raw, plan, scope, credentials, proxy = await start_target(tmp_path)
    try:
        spec, plan = RgsScenarioSpec.model_validate(raw), RgsTargetPlan.model_validate(
            plan
        )
        checkpoint = tmp_path / "capture.json"
        partial = await observe_rgs(
            spec,
            plan,
            ScopedTargetClient(
                TargetScope.model_validate(scope),
                credential_root=credentials,
                proxy=proxy,
                should_stop=lambda: bool(target.requests),
            ),
            checkpoint_path=checkpoint,
        )
        assert assess_rgs(spec, partial).outcome == "incomplete"
        assert target.requests == [raw["scenarios"][0]["operations"][0]["id"]]
        saved = checkpoint.read_bytes()
        # A new client and collector recover the captured checkpoint, without
        # relying on the stopped process or a test-side receipt dictionary.
        complete = await observe_rgs(
            spec,
            plan,
            ScopedTargetClient(
                TargetScope.model_validate(scope),
                credential_root=credentials,
                proxy=proxy,
            ),
            checkpoint_path=checkpoint,
        )
        assert assess_rgs(spec, complete).outcome == "consistent"
        assert len(target.requests) == len(set(target.requests))
        assert target.db.execute("SELECT COUNT(*) FROM effects").fetchone() == (2,)
        assert saved != checkpoint.read_bytes()
        # A finished capture is immutable input for the normal profile/retest.
        previous = list(target.requests)
        await observe_rgs(
            spec,
            plan,
            ScopedTargetClient(
                TargetScope.model_validate(scope),
                credential_root=credentials,
                proxy=proxy,
            ),
            checkpoint_path=checkpoint,
        )
        assert target.requests == previous
    finally:
        await runner.cleanup()
        target.db.close()


async def test_http_and_websocket_share_real_rate_and_total_budgets(tmp_path):
    from moonmind.security.target_assessments import (
        ScopedTargetClient,
        TargetRequest,
        TargetScope,
        TargetStopped,
    )

    target, runner, _, plan, scope, credentials, proxy = await start_target(tmp_path)
    try:
        scope["maxRequests"] = 3
        scope["web"]["maxRequestsPerSecond"] = 5
        client = ScopedTargetClient(
            TargetScope.model_validate(scope), credential_root=credentials, proxy=proxy
        )
        await client.exchange(
            TargetRequest.model_validate(plan["operations"][0]["request"])
        )
        await client.exchange(
            TargetRequest.model_validate(plan["operations"][1]["request"])
        )
        assert target.times[1] - target.times[0] >= 0.35
        assert client.requests_sent == 3  # HTTP, handshake, WebSocket message
        with pytest.raises(TargetStopped, match="request_budget"):
            await client.exchange(
                TargetRequest.model_validate(plan["operations"][2]["request"])
            )
        assert len(target.requests) == 2
        limited = ScopedTargetClient(
            TargetScope.model_validate(scope),
            credential_root=credentials,
            proxy=proxy,
            max_seconds=0.03,
        )
        await limited.exchange(
            TargetRequest.model_validate(plan["operations"][0]["request"])
        )
        with pytest.raises(TargetStopped, match="time_budget"):
            await limited.exchange(
                TargetRequest.model_validate(plan["operations"][2]["request"])
            )
        assert limited.requests_sent == 1
    finally:
        await runner.cleanup()
        target.db.close()


async def test_concurrent_shared_requests_cannot_overrun_the_total_budget(tmp_path):
    from moonmind.security.target_assessments import (
        ScopedTargetClient,
        TargetRequest,
        TargetScope,
        TargetStopped,
    )

    target, runner, _, plan, scope, credentials, proxy = await start_target(tmp_path)
    try:
        scope["maxRequests"] = 2
        scope["web"]["maxRequestsPerSecond"] = 10
        client = ScopedTargetClient(
            TargetScope.model_validate(scope), credential_root=credentials, proxy=proxy
        )
        requests = [
            TargetRequest.model_validate(
                {**plan["operations"][0]["request"], "id": "concurrent-" + str(i)}
            )
            for i in range(3)
        ]
        results = await asyncio.gather(
            *(client.exchange(r) for r in requests), return_exceptions=True
        )
        assert sum(isinstance(r, TargetStopped) for r in results) == 1
        assert client.requests_sent == 2
        assert len(target.requests) == 2
        assert target.times[1] - target.times[0] >= 0.08
    finally:
        await runner.cleanup()
        target.db.close()


async def test_missing_capture_reconciles_the_effect_owner_before_any_replay(tmp_path):
    from moonmind.security.rgs_assessment import RgsScenarioSpec, assess_rgs
    from moonmind.security.rgs_target import RgsTargetPlan, observe_rgs
    from moonmind.security.target_assessments import ScopedTargetClient, TargetScope

    target, runner, raw, plan, scope, credentials, proxy = await start_target(tmp_path)
    try:
        spec, plan = RgsScenarioSpec.model_validate(raw), RgsTargetPlan.model_validate(
            plan
        )
        first_capture = tmp_path / "first-host" / "capture.json"
        await observe_rgs(
            spec,
            plan,
            ScopedTargetClient(
                TargetScope.model_validate(scope),
                credential_root=credentials,
                proxy=proxy,
                should_stop=lambda: bool(target.requests),
            ),
            checkpoint_path=first_capture,
        )
        assert target.db.execute("SELECT COUNT(*) FROM effects").fetchone() == (1,)
        previous = list(target.requests)
        # The successor cannot access its predecessor's output mount. It
        # still has the admitted inputs and the independent target store.
        recovered = await observe_rgs(
            spec,
            plan,
            ScopedTargetClient(
                TargetScope.model_validate(scope),
                credential_root=credentials,
                proxy=proxy,
            ),
            checkpoint_path=tmp_path / "replacement-host" / "capture.json",
        )
        assert target.requests == previous
        assert target.db.execute("SELECT COUNT(*) FROM effects").fetchone() == (1,)
        assert recovered.traces[0].wallet_effects
        assert recovered.collection["stopReason"] == "prior_effects_without_checkpoint"
        assert assess_rgs(spec, recovered).status == "partial"
        recovered = await observe_rgs(
            spec,
            plan,
            ScopedTargetClient(
                TargetScope.model_validate(scope),
                credential_root=credentials,
                proxy=proxy,
            ),
            checkpoint_path=tmp_path / "replacement-host" / "capture.json",
        )
        assert target.requests == previous
        assert recovered.collection["stopReason"] == "prior_effects_without_checkpoint"
        assert first_capture.exists()
        retain_target_evidence(
            "missing-checkpoint", assess_rgs(spec, recovered), recovered, target
        )
    finally:
        await runner.cleanup()
        target.db.close()


async def test_real_target_capture_through_existing_job_and_retained_report(
    environment, tmp_path, monkeypatch
):
    from moonmind.security.rgs_assessment import RgsScenarioSpec
    from moonmind.security.rgs_target import RgsTargetPlan, observe_rgs
    from moonmind.security.target_assessments import ScopedTargetClient, TargetScope

    target, runner, raw, plan, scope, credentials, proxy = await start_target(tmp_path)
    try:
        capture = await observe_rgs(
            RgsScenarioSpec.model_validate(raw),
            RgsTargetPlan.model_validate(plan),
            ScopedTargetClient(
                TargetScope.model_validate(scope),
                credential_root=credentials,
                proxy=proxy,
            ),
            checkpoint_path=tmp_path / "capture.json",
        )
        job = Job(environment, tmp_path, monkeypatch)
        _, argv = await job.launch(
            "real-target-report",
            {"spec": "spec/spec.json", "traces": "traces/traces.json"},
            {
                "spec": json.dumps(raw).encode(),
                "traces": capture.model_dump_json(by_alias=True).encode(),
            },
        )
        process = job.process(argv)
        _, stderr = await asyncio.to_thread(process.communicate, timeout=30)
        assert process.returncode == 0, stderr.decode()
        ref = await job.publish(terminal_state="succeeded", exit_code=0)
        result = await job.assess(ref, "real-target-retained-report")
        summary = result["scannerSummaries"][0]
        assert (summary["outcome"], summary["coverage"]) == ("consistent", "complete")
        assert "authorization_matrix_not_exercised" not in summary["limitations"]
        assert "transport_not_exercised" not in summary["limitations"]
        assert set(json.loads(job.published[ref])["job"]["inputRefs"]) == {
            "spec",
            "traces",
        }
        assert len(target.requests) == len(set(target.requests))
    finally:
        await runner.cleanup()
        target.db.close()


async def test_cli_host_loss_after_wallet_commit_never_resends_the_attempt(tmp_path):
    target, runner, raw, plan, scope, credentials, proxy = await start_target(tmp_path)
    process = None
    try:
        target.hold_response = asyncio.Event()
        spec_file, plan_file = tmp_path / "spec.json", tmp_path / "plan.json"
        capture, report = tmp_path / "capture.json", tmp_path / "report.json"
        spec_file.write_text(json.dumps(raw))
        plan_file.write_text(json.dumps(plan))
        # Relocate the real workload file mount for this isolated process.
        # Authentication and all network/persistence work remain production.
        command = [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; from moonmind.security.rgs_target import main; sys.exit(main(sys.argv[2:], credential_root=Path(sys.argv[1])))",
            str(credentials),
            "--spec",
            str(spec_file),
            "--plan",
            str(plan_file),
            "--capture",
            str(capture),
            "--output",
            str(report),
        ]
        env = {
            **os.environ,
            "MOONMIND_TARGET_ASSESSMENT_SCOPE": json.dumps(scope),
            "MOONMIND_EGRESS_POLICY_REF": scope["web"]["egressPolicyRef"],
            "MOONMIND_EGRESS_PROXY": proxy,
        }
        process = await asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def committed():
            while not target.requests:
                if process.returncode is not None:
                    _, stderr = await process.communicate()
                    pytest.fail(stderr.decode())
                await asyncio.sleep(0.01)

        await asyncio.wait_for(committed(), timeout=10)
        assert target.db.execute("SELECT COUNT(*) FROM effects").fetchone() == (1,)
        process.send_signal(signal.SIGKILL)
        await process.communicate()
        assert (
            json.loads(capture.read_bytes())["traces"][0]["operations"][0]["decision"]
            == "unknown"
        )
        assert json.loads(report.read_bytes())["status"] == "partial"
        target.hold_response.set()
        process = await asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        assert process.returncode == 0, stderr.decode()
        recovered = json.loads(report.read_bytes())
        assert (recovered["status"], recovered["outcome"]) == ("partial", "incomplete")
        assert len(target.requests) == len(set(target.requests))
        assert target.db.execute("SELECT COUNT(*) FROM effects").fetchone() == (2,)
        assert any(
            c["rule"] == "target-access" and c["observed"] == "unknown"
            for c in recovered["checks"]
        )
        assert all(
            token not in report.read_text() + capture.read_text() + stderr.decode()
            for token in target.secrets.values()
        )
        from moonmind.security.rgs_assessment import RgsAssessment, RgsTraceBundle

        retain_target_evidence(
            "interrupted",
            RgsAssessment.model_validate_json(report.read_bytes()),
            RgsTraceBundle.model_validate_json(capture.read_bytes()),
            target,
        )
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.communicate()
        if target.hold_response is not None:
            target.hold_response.set()
        await runner.cleanup()
        target.db.close()


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
async def test_target_cli_capture_and_actual_terminal_outcome_are_retained(
    environment, tmp_path, monkeypatch, terminal
):
    from moonmind.security.rgs_assessment import RgsScenarioSpec
    from moonmind.security.rgs_target import RgsTargetPlan, observe_rgs
    from moonmind.security.target_assessments import ScopedTargetClient, TargetScope
    from tests.integration.security.test_rgs_assessment_jobs import PROFILE

    target, runner, raw, plan, scope, credentials, proxy = await start_target(tmp_path)
    try:
        capture = await observe_rgs(
            RgsScenarioSpec.model_validate(raw),
            RgsTargetPlan.model_validate(plan),
            ScopedTargetClient(
                TargetScope.model_validate(scope),
                credential_root=credentials,
                proxy=proxy,
            ),
            checkpoint_path=tmp_path / "capture.json",
        )
        job = Job(environment, tmp_path, monkeypatch)
        profile = copy.deepcopy(PROFILE)
        profile["entrypoint"] = ["python", "-m", "moonmind.security.rgs_target"]
        profile["environment"] = {
            "MOONMIND_TARGET_ASSESSMENT_SCOPE": json.dumps(scope),
            "MOONMIND_EGRESS_PROXY": proxy,
            "MOONMIND_EGRESS_POLICY_REF": scope["web"]["egressPolicyRef"],
        }
        profile["modes"][0]["arguments"] = [
            {"name": n, "type": "input", "required": True}
            for n in ("spec", "plan", "resume")
        ]
        profile["modes"][0]["argv"] = [
            "--spec={spec}",
            "--plan={plan}",
            "--resume={resume}",
            "--capture=/outputs/rgs-traces.json",
            "--output=/outputs/rgs-assessment.json",
        ]
        profile["outputs"] = [
            {"name": "assessment", "relativePath": "rgs-assessment.json"},
            {"name": "capture", "relativePath": "rgs-traces.json"},
        ]
        job.settings = resolve_container_backend_settings(
            {
                IMAGE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {
                            "sourceRef": "moonmind",
                            "image": "ghcr.io/moonladderstudios/moonmind:latest",
                        }
                    ]
                ),
                WORKLOAD_PROFILES_ENV_KEY: json.dumps([profile]),
            }
        )
        create, argv = await job.launch(
            "target-capture-" + terminal,
            {n: n + "/" + n + ".json" for n in ("spec", "plan", "resume")},
            {
                "spec": json.dumps(raw).encode(),
                "plan": json.dumps(plan).encode(),
                "resume": capture.model_dump_json(by_alias=True).encode(),
            },
        )
        assert create[create.index("--network") + 1] == "none"
        # A complete capture is consumed without network/credential effects.
        # Run exactly the rendered module command and fixed profile environment.
        for before, after in (
            ("/inputs/", str(job.storage.inputs) + "/"),
            ("/outputs/", str(job.storage.outputs) + "/"),
        ):
            argv = [arg.replace(before, after) for arg in argv]
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *argv,
            env={**os.environ, **profile["environment"]},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        assert process.returncode == 0, stderr.decode()
        ref = await job.publish(
            terminal_state=terminal, exit_code=0 if terminal == "succeeded" else None
        )
        report = json.loads(job.published[ref])
        assert report["job"]["terminalState"] == terminal
        assert report["status"] == (
            "complete" if terminal == "succeeded" else "partial"
        )
        capture_ref = report["job"]["inputRefs"]["capture"]
        assert (
            json.loads(job.published[capture_ref])["schemaVersion"] == "rgs-traces/v1"
        )
        outputs = await job.assess(ref, "target-terminal-" + terminal)
        assert outputs["scannerSummaries"][0]["coverage"] == (
            "complete" if terminal == "succeeded" else "partial"
        )
        assert len(target.requests) == len(set(target.requests))
    finally:
        await runner.cleanup()
        target.db.close()
