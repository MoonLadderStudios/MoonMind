"""Controlled Tactics transport for MoonMind#5109; never an Unreal validator.

The fixture owns its dependency/proof decisions. MoonMind carries its bytes and
uses the resolved acceptance contract, without implementing Tactics policy.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

CHECKS = ("prepare", "build", "targeted-test")
DEPENDENCIES = {
    "prepare": ("Content", "profile"),
    "build": ("source", "profile", "toolchain"),
    "targeted-test": ("source", "Content", "profile", "selection", "topology"),
}


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def bindings(root, options):
    return {
        "source": digest((root / "Source/fixture.py").read_bytes()),
        "Content": digest((root / "Content/fixture.txt").read_bytes()),
        "profile": options.get("profile", "Demo"),
        "selection": options.get("selection", ["Tactics.Inventory.Minimal"]),
        "toolchain": "controlled-python-compiler",
        "topology": "Linux/headless/single-process",
    }


def inspect(root, options):
    bound = bindings(root, options)
    return {
        "owner": "controlled Tactics tools/ci contract",
        "subject": {
            "repository": "MoonLadderStudios/Tactics",
            "revision": git(root, "rev-parse", "HEAD"),
            "contentDigest": "git-tree:" + git(root, "rev-parse", "HEAD^{tree}"),
        },
        "bindings": bound,
        "scopes": {
            check: {key: bound[key] for key in DEPENDENCIES[check]} for check in CHECKS
        },
        "capabilities": {
            "build": True,
            "headless": True,
            "gameplay": False,
            "rendered": False,
        },
    }


def produce(root, options):
    admitted = inspect(root, options)
    outputs = root / "artifacts"
    outputs.mkdir(exist_ok=True)
    exit_code = 0
    for check in options["checks"]:
        report = {**admitted, "check": check, "status": "passed", "exitCode": 0}
        try:
            if check == "prepare":
                if options.get("unavailable"):
                    report.update(
                        status="unavailable",
                        reason="Tactics Content authoring dependency is unavailable in this checkout.",
                        nextRoute="Use the existing authorized Content authoring job; retain this candidate.",
                        verifiableBy="Tactics Content authoring job",
                        attempts=1,
                    )
                else:
                    assert (root / "Content/fixture.txt").read_text().strip()
            elif check == "build":
                compile((root / "Source/fixture.py").read_text(), "fixture.py", "exec")
            else:
                namespace = {}
                exec((root / "Source/fixture.py").read_text(), namespace)
                report["selectedTests"] = []
                for selected in admitted["bindings"]["selection"]:
                    assert namespace["value"]() == 42, "controlled selected test failed"
                    report["selectedTests"].append(selected)
                report["selectedTestCount"] = len(report["selectedTests"])
        except (SyntaxError, AssertionError) as exc:
            report.update(status="failed", exitCode=1, reason=str(exc))
            exit_code = 1
        (outputs / f"{check}.json").write_text(
            json.dumps(report, sort_keys=True) + "\n"
        )
        print(json.dumps({"check": check, "status": report["status"]}), flush=True)
    return exit_code


def compatibility(root, options):
    """Emulate the external owner, including an independently read byte digest."""
    current = inspect(root, options)
    decisions = {}
    for check, path in options["receipts"].items():
        raw = Path(path).read_bytes()
        previous = json.loads(raw)
        decisions[check] = {
            "compatible": previous["scopes"][check] == current["scopes"][check],
            "receiptDigest": digest(raw),
            "testedSubject": previous["subject"],
            "status": previous["status"],
        }
    print(json.dumps({**current, "decisions": decisions}, sort_keys=True))


if __name__ == "__main__":
    action, root, raw_options = sys.argv[1:]
    root, options = Path(root), json.loads(raw_options)
    if action == "produce":
        sys.exit(produce(root, options))
    if action == "compatibility":
        compatibility(root, options)
    else:
        print(json.dumps(inspect(root, options), sort_keys=True))
