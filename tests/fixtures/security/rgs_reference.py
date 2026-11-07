"""Synthetic wallet/game with fault injection; no network or customer funds."""

from __future__ import annotations

import copy
from threading import RLock


class Wallet:
    def __init__(self, balances, fault=None):
        self.balances = dict(balances)
        self.effects = []
        self.receipts = {}
        self.lock = RLock()
        self.fault = fault

    def apply(self, tx, kind, account, amount):
        with self.lock:
            previous = self.receipts.get(tx)
            duplicate_bug = (kind == "debit" and self.fault == "double-debit") or (
                kind == "credit" and self.fault == "double-credit"
            )
            if previous and not duplicate_bug:
                return previous
            effect = {
                "id": f"effect-{len(self.effects)}",
                "transactionId": tx,
                "kind": kind,
                "accountRef": account,
                "amount": amount,
            }
            self.effects.append(effect)
            self.balances[account] += amount
            self.receipts[tx] = effect
            return effect


class GameServer:
    """A small reference target, independent of the assessment's assertions.

    Settlement can lose its acknowledgement after the wallet confirms it.
    Retrying consults that wallet receipt and completes game/audit state under
    the same transaction; it never invents a fresh payment identity.
    """

    def __init__(self, spec, scenario, fault=None):
        self.spec, self.scenario = spec, scenario
        self.wallet = Wallet(scenario["initialBalances"], fault)
        self.wagers = {w["id"]: w for w in scenario["wagers"]}
        self.outcomes = {w: "unplaced" for w in self.wagers}
        self.accepted = {}
        self.audit = []
        self.lock = RLock()
        self.fault = fault

    def request(self, request):
        with self.lock:
            tx, kind = request["transactionId"], request["kind"]
            wager = self.wagers.get(request.get("wagerRef"))
            fingerprint = (kind, request.get("wagerRef"))
            if tx in self.accepted:
                if self.accepted[tx] != fingerprint:
                    return "denied"
                if wager and kind in {"wager", "cashout", "rollback"}:
                    self._payment(tx, kind, wager)
                return "accepted"
            at = request["processedAt"]
            boundary = self.spec.get(
                {"wager": "bettingClose", "cashout": "cashoutClose"}.get(kind, "")
            )
            if boundary:
                at = request[boundary["recognition"] + "At"]
                close = next(
                    e["at"]
                    for e in self.scenario["events"]
                    if e["event"] == boundary["event"]
                )
                if at > close or (at == close and boundary["tie"] == "deny"):
                    return "denied"
            state = self.spec["initialState"]
            for event in self.scenario["events"]:
                if event["at"] > at or (
                    boundary
                    and event["at"] == at
                    and boundary["tie"] == "allow"
                    and event["event"] == boundary["event"]
                ):
                    break
                transition = next(
                    t
                    for t in self.spec["transitions"]
                    if t["event"] == event["event"] and t["fromState"] == state
                )
                state = transition["toState"]
            if state not in self.spec["allowedStates"][kind]:
                return "denied"
            if wager:
                previous = self.outcomes[wager["id"]]
                if (kind == "wager" and previous != "unplaced") or (
                    kind != "wager" and previous != "active"
                ):
                    return "denied"
                if kind == "rollback" and (
                    self.spec["interruption"]["policy"] != "rollback"
                    or not any(
                        e["event"] == self.spec["interruption"]["event"]
                        and e["at"] <= at
                        for e in self.scenario["events"]
                    )
                ):
                    return "denied"
                effect = self._payment(tx, kind, wager)
                if request["id"] == "cash-lost-ack" and not self.wallet.receipts.get(
                    "interrupted"
                ):
                    self.wallet.receipts["interrupted"] = True
                    # Game state/audit intentionally lag the already committed
                    # wallet effect until the retry reconciles it.
                    self.audit = [e for e in self.audit if e["transactionId"] != tx]
                    return "unknown"
                self.outcomes[wager["id"]] = {
                    "wager": "active",
                    "cashout": "cashed_out",
                    "rollback": "rolled_back",
                    "settle": "settled",
                }[kind]
            self.accepted[tx] = fingerprint
            return "accepted"

    def _payment(self, tx, kind, wager):
        amount = {
            "wager": -wager["stake"],
            "cashout": wager["cashoutCredit"],
            "rollback": wager["stake"],
            "settle": wager["settlementCredit"],
        }[kind]
        if not amount:
            return None
        effect = self.wallet.apply(
            tx,
            {
                "wager": "debit",
                "cashout": "credit",
                "rollback": "rollback",
                "settle": "credit",
            }[kind],
            wager["accountRef"],
            amount,
        )
        if not any(e["id"] == effect["id"] for e in self.audit):
            self.audit.append(copy.deepcopy(effect))
        return effect

    def trace(self):
        receipts = []
        for operation in self.scenario["operations"]:
            receipts.append(
                {"operation": operation["id"], "decision": self.request(operation)}
            )
        for i, receipt in enumerate(receipts):
            if receipt["decision"] == "unknown":
                tx = self.scenario["operations"][i]["transactionId"]
                for later, request in zip(
                    receipts[i + 1 :], self.scenario["operations"][i + 1 :]
                ):
                    if (
                        request["transactionId"] == tx
                        and later["decision"] == "accepted"
                    ):
                        receipt["reconciledBy"] = later["operation"]
                        break
        state = self.spec["initialState"]
        for event in self.scenario["events"]:
            state = next(
                t["toState"]
                for t in self.spec["transitions"]
                if t["event"] == event["event"] and t["fromState"] == state
            )
        trace = {
            "scenario": self.scenario["id"],
            "evidenceRefs": ["fixture:" + self.scenario["id"]],
            "operations": receipts,
            "walletEffects": self.wallet.effects,
            "auditEffects": self.audit,
            "balances": self.wallet.balances,
            "wagerOutcomes": self.outcomes,
            "roundState": state,
            "complete": True,
        }
        if self.fault == "missing-audit":
            trace["auditEffects"] = []
        if self.fault == "wrong-balance":
            trace["balances"]["wallet-a"] += 100
        if self.fault == "wrong-outcome":
            trace["wagerOutcomes"]["first"] = "active"
        if self.fault == "wrong-round":
            trace["roundState"] = "open"
        return trace


def reference_traces(spec, *, fault=None):
    from moonmind.security.rgs_assessment import RgsTraceBundle

    data = spec.model_dump(mode="json", by_alias=True)
    return RgsTraceBundle.model_validate(
        {
            "sourceRef": "fixture:synthetic-rgs",
            "traces": [
                GameServer(data, scenario, fault).trace()
                for scenario in data["scenarios"]
            ],
        }
    )
