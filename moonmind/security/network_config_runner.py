"""Batfish answers for sanitized network configuration exports.

Runs inside the network-config analyzer image with no network. The worker
writes this file, the sanitized snapshot and the admitted flows into the job's
own output directory; the raw exports are never read here. The script starts
the image's Batfish service on loopback, asks a fixed set of questions and
writes their answers unchanged. It uses only the standard library and the
image's pybatfish, because the analyzer image carries no MoonMind package.

Usage: ``python3 -I runner.py <snapshot dir> <flows.json> <native.json>``
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

NATIVE_VERSION = "network-config-native/v1"
#: Where the published Batfish all-in-one image keeps its service.
SERVICE_JAR = "/allinone-bundle.jar"
QUESTION_TEMPLATES = "/questions"
#: Clear-text protocols an inbound interface filter is asked about.
CLEARTEXT_PROTOCOLS = {
    "telnet": ("TCP", "23"),
    "ftp": ("TCP", "21"),
    "tftp": ("UDP", "69"),
}
#: Filters applied inbound on any interface, in Batfish's filter specifier.
INBOUND_FILTERS = "@in(/.*/)"
NODE_PROPERTIES = "Configuration_Format,NTP_Servers,Logging_Servers,Interfaces"
INTERFACE_PROPERTIES = "Incoming_Filter_Name,Outgoing_Filter_Name,Active"
STARTUP_SECONDS = 240
MAX_ROWS = 20000


def _rows(answer) -> list | dict:
    frame = answer.frame()
    rows = json.loads(frame.to_json(orient="records", default_handler=str))
    if len(rows) > MAX_ROWS:
        return {"error": "row_limit", "rows": rows[:MAX_ROWS]}
    return rows


def _ask(answers: dict, name: str, build) -> None:
    try:
        answers[name] = _rows(build().answer())
    except Exception as exc:  # recorded, never silently clean
        answers[name] = {"error": type(exc).__name__}


def _session():
    from pybatfish.client.session import Session

    deadline = time.monotonic() + STARTUP_SECONDS
    while True:
        try:
            session = Session(host="localhost")
            session.set_network("moonmind")
            return session
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def analyze(snapshot: str, flows: list[dict]) -> dict:
    from pybatfish.datamodel import HeaderConstraints

    session = _session()
    versions = {}
    try:
        versions = {
            str(key): str(value)
            for key, value in session.get_component_versions().items()
        }
    except Exception:
        versions = {}
    session.init_snapshot(snapshot, name="exports", overwrite=True)
    q = session.q
    answers: dict = {}
    _ask(answers, "fileParseStatus", q.fileParseStatus)
    _ask(answers, "parseWarning", q.parseWarning)
    _ask(answers, "initIssues", q.initIssues)
    _ask(
        answers, "nodeProperties", lambda: q.nodeProperties(properties=NODE_PROPERTIES)
    )
    _ask(
        answers,
        "interfaceProperties",
        lambda: q.interfaceProperties(properties=INTERFACE_PROPERTIES),
    )
    _ask(answers, "definedStructures", q.definedStructures)
    _ask(answers, "undefinedReferences", q.undefinedReferences)
    _ask(answers, "filterLineReachability", q.filterLineReachability)
    for name, (protocol, port) in CLEARTEXT_PROTOCOLS.items():
        _ask(
            answers,
            f"searchFilters:{name}",
            lambda protocol=protocol, port=port: q.searchFilters(
                headers=HeaderConstraints(ipProtocols=[protocol], dstPorts=port),
                filters=INBOUND_FILTERS,
                action="permit",
            ),
        )
    evaluated = []
    for flow in flows:
        result: dict = {"flow": flow}
        _ask(
            result,
            "testFilters",
            lambda flow=flow: q.testFilters(
                headers=HeaderConstraints(
                    dstIps=flow["host"],
                    dstPorts=str(flow["port"]),
                    ipProtocols=[flow["protocol"].upper()],
                ),
                filters=INBOUND_FILTERS,
            ),
        )
        evaluated.append(result)
    return {
        "schemaVersion": NATIVE_VERSION,
        "analyzer": {"name": "batfish", "versions": versions},
        "answers": answers,
        "flows": evaluated,
    }


def main(argv: list[str]) -> int:
    snapshot, flows_path, output = argv[1], argv[2], argv[3]
    with open(flows_path, encoding="utf-8") as handle:
        flows = json.load(handle)
    os.makedirs("/tmp/batfish", exist_ok=True)
    with open("/tmp/batfish/service.log", "w", encoding="utf-8") as log:
        service = subprocess.Popen(
            [
                "java",
                "-Xmx1536m",
                "-cp",
                SERVICE_JAR,
                "org.batfish.allinone.Main",
                "-runclient",
                "false",
                "-coordinatorargs",
                f"-templatedirs {QUESTION_TEMPLATES} -periodassignworkms=5",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd="/tmp/batfish",
        )
        try:
            try:
                document = analyze(snapshot, flows)
            except Exception as exc:
                document = {
                    "schemaVersion": NATIVE_VERSION,
                    "analyzer": {"name": "batfish", "versions": {}},
                    "error": type(exc).__name__,
                    "answers": {},
                    "flows": [],
                }
            with open(output, "w", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True)
        finally:
            service.terminate()
            try:
                service.wait(timeout=20)
            except subprocess.TimeoutExpired:
                service.kill()
    return 0 if "error" not in document else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
