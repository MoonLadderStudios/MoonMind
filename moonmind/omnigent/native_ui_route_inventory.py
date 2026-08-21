"""Generate the exact stock Omnigent network inventory.

Source issue: MoonLadderStudios/MoonMind#3635.

The stock service routes are parsed from the pinned Omnigent FastAPI sources;
they are never copied into a MoonMind allowlist.  The independent result is
then joined with the binding-scoped facade policy.  Unknown entries receive a
stable fail-closed classification, so an upstream route addition changes the
inventory digest and fails the exact-artifact gate until reviewed.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from moonmind.omnigent.native_ui_compat import (
    CODE_TRANSPORT_UNSUPPORTED,
    DISPOSITION_SERVED,
    NATIVE_UI_ROUTES,
    TRANSPORT_SSE,
    TRANSPORT_WEBSOCKET,
    NativeUiRoute,
    route_policy,
)

INVENTORY_SCHEMA_VERSION = "moonmind.omnigent.native-ui-route-inventory/v2"

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_ROUTE_METHODS = _HTTP_METHODS | {"websocket"}
_PARAMETER = re.compile(r"\{(?P<name>[^}:]+)(?::[^}]+)?\}")
_UI_ROUTE_LITERAL = re.compile(r"[\"'`](/v1/[A-Za-z0-9_?&=./${}:~-]+)")
_TYPE_COMPARISON = re.compile(
    r"(?:msg|control)\.get\([\"']type[\"']\)\s*(?:==|!=)\s*[\"']([^\"']+)"
)

_SERVER_ROUTE_ROOT = Path("omnigent/omnigent/server/routes")
_SERVER_APP = Path("omnigent/omnigent/server/app.py")
_UI_ROOT = Path("omnigent/web/src")


class NativeUiRouteInventoryError(ValueError):
    """Raised when exact stock sources cannot produce an inventory."""


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + sha256(payload).hexdigest()


def _file_digest(root: Path, paths: Iterable[Path]) -> str:
    entries: list[dict[str, str]] = []
    for path in sorted(set(paths)):
        if not path.is_file():
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
        )
    if not entries:
        raise NativeUiRouteInventoryError("exact artifact input set is empty")
    return _canonical_digest(entries)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _ui_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".ts", ".tsx", ".js", ".jsx"}
        and ".test." not in path.name
        and ".spec." not in path.name
    )


def _git_revision(repository: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise NativeUiRouteInventoryError(
            "pinned Omnigent git revision is unavailable"
        ) from exc
    revision = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise NativeUiRouteInventoryError("pinned Omnigent revision is invalid")
    return revision


def _decorated_routes(path: Path, *, prefix: str) -> list[dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    routes: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        for decorator in getattr(node, "decorator_list", ()):  # functions only
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            if decorator.func.attr not in _ROUTE_METHODS:
                continue
            if not (
                decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                raise NativeUiRouteInventoryError(
                    f"stock route path is not a literal at {path}:{decorator.lineno}"
                )
            raw_path = decorator.args[0].value
            public_path = (prefix.rstrip("/") + "/" + raw_path.lstrip("/")).replace(
                "//", "/"
            )
            method = decorator.func.attr.upper()
            handler_source = ast.get_source_segment(source, node) or ""
            if method == "WEBSOCKET":
                transport = TRANSPORT_WEBSOCKET
            elif method == "GET" and (
                "StreamingResponse" in handler_source
                or "text/event-stream" in handler_source
            ):
                transport = TRANSPORT_SSE
            else:
                transport = "http"
            routes.append(
                {
                    "method": method,
                    "path": public_path,
                    "transport": transport,
                    "sourceFile": path.as_posix(),
                    "sourceLine": decorator.lineno,
                    "handlerSource": handler_source,
                }
            )
    return routes


def _stock_routes(repo_root: Path) -> list[dict[str, Any]]:
    route_root = repo_root / _SERVER_ROUTE_ROOT
    if not route_root.is_dir():
        raise NativeUiRouteInventoryError(
            "pinned Omnigent server routes are not materialized"
        )
    routes: list[dict[str, Any]] = []
    for path in _python_files(route_root):
        # Built-in account/OIDC/device routes are mounted outside /v1 and are
        # not part of a workflow binding's UI/service authority.
        if path.name in {"accounts_auth.py", "auth.py", "device_auth.py"}:
            continue
        routes.extend(_decorated_routes(path, prefix="/v1"))

    # App-owned stock bootstrap/liveness routes do not live in a route module.
    for route in _decorated_routes(repo_root / _SERVER_APP, prefix=""):
        if route["path"] == "/health" or route["path"].startswith("/v1/"):
            routes.append(route)

    unique: dict[str, dict[str, Any]] = {}
    for route in routes:
        route["sourceFile"] = Path(route["sourceFile"]).relative_to(repo_root).as_posix()
        key = f"{route['method']} {route['path']}"
        route["routeKey"] = key
        prior = unique.get(key)
        if prior is not None:
            raise NativeUiRouteInventoryError(
                f"duplicate exact stock route declaration: {key}"
            )
        unique[key] = route
    return [unique[key] for key in sorted(unique)]


def _sample_path(path: str) -> str:
    def substitute(match: re.Match[str]) -> str:
        name = match.group("name")
        if name == "session_id":
            return "binding-1"
        if name == "terminal_id":
            return "terminal-1"
        if name in {"path", "relative_path", "file_path", "res_path"}:
            return "sample/path.txt"
        return "sample"

    return _PARAMETER.sub(substitute, path).strip("/")


def _matched_facade_route(method: str, path: str) -> NativeUiRoute | None:
    candidate = _sample_path(path)
    for route in NATIVE_UI_ROUTES:
        if method == "WEBSOCKET":
            if route.transport != TRANSPORT_WEBSOCKET:
                continue
        elif route.transport == TRANSPORT_WEBSOCKET or method not in route.methods:
            continue
        if route.pattern is not None and route.pattern.fullmatch(candidate):
            return route
    return None


def _fail_closed_policy(route: dict[str, Any]) -> dict[str, Any]:
    mutation = route["method"] not in {"GET", "WEBSOCKET"}
    long_lived = route["transport"] == TRANSPORT_WEBSOCKET
    return {
        "publicRoute": (
            "/api/workflow-chat-bindings/{chatBindingId}/omnigent"
            + route["path"]
        ),
        "callerPermission": (
            "binding_owner_or_explicit_mutation_grant"
            if mutation
            else "binding_owner_or_explicit_read_grant"
        ),
        "requiredCapability": "unsupported",
        "readOnly": not mutation,
        "requestBounds": {
            "maxPathBytes": 4096,
            "maxQueryItems": 32,
            "maxBodyBytes": 0,
            "maxFrameBytes": 0,
        },
        "responseBounds": {"maxBodyBytes": 0, "maxItems": 0, "maxFrameBytes": 0},
        "identityVirtualization": "provider_session_id_to_chat_binding_id",
        "reconnect": (
            "reject_before_upgrade" if long_lived else "reject_before_upstream"
        ),
        "idempotency": "no_upstream_delivery",
        "historicalRead": "unsupported",
        "unsupportedBehavior": CODE_TRANSPORT_UNSUPPORTED,
        "mutationReceipt": None,
    }


def _classify_route(route: dict[str, Any]) -> dict[str, Any]:
    matched = _matched_facade_route(route["method"], route["path"])
    if matched is None or matched.disposition != DISPOSITION_SERVED:
        classification = "fail_closed"
        policy = _fail_closed_policy(route)
        facade_operation = None
    else:
        classification = "binding_scoped"
        policy = route_policy(matched)
        policy["publicRoute"] = (
            "/api/workflow-chat-bindings/{chatBindingId}/omnigent"
            + route["path"]
        )
        policy["requiredCapability"] = matched.capability
        policy["readOnly"] = not matched.mutation
        facade_operation = matched.name
    return {
        key: value
        for key, value in {
            "routeKey": route["routeKey"],
            "method": route["method"],
            "path": route["path"],
            "transport": route["transport"],
            "sourceFile": route["sourceFile"],
            "sourceLine": route["sourceLine"],
            "classification": classification,
            "facadeOperation": facade_operation,
            **policy,
        }.items()
    }


def _sent_json_types(handler_source: str) -> set[str]:
    """Extract literal ``type`` values nested in WebSocket send calls."""

    try:
        tree = ast.parse(handler_source)
    except SyntaxError:
        return set()
    result: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        if function_name not in {"_send", "send_text"}:
            continue
        for descendant in ast.walk(node):
            if not isinstance(descendant, ast.Dict):
                continue
            for key, value in zip(descendant.keys, descendant.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "type"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    result.add(value.value)
    return result


def _websocket_protocol(route: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    source = str(route.pop("handlerSource", ""))
    client = set(_TYPE_COMPARISON.findall(source))
    server = _sent_json_types(source)
    if "receive_bytes" in source or 'message.get("bytes")' in source:
        client.add("binary_audio" if "dictation" in route["path"] else "binary_input")
    if "send_bytes" in source:
        server.add("binary_output")
    # Terminal and dictation routes delegate framing to module helpers. Parse
    # that exact, digest-bound module rather than maintaining message lists.
    if (
        "/terminals/" in route["path"] and route["path"].endswith("/attach")
    ) or route["path"] == "/v1/dictation/stream":
        module_source = (repo_root / route["sourceFile"]).read_text(encoding="utf-8")
        client.update(_TYPE_COMPARISON.findall(module_source))
        server.update(_sent_json_types(module_source))
        if '"type": "resize"' in module_source or '"type") == "resize"' in module_source:
            client.add("resize")
        if 'get("bytes")' in module_source:
            client.add(
                "binary_audio" if "dictation" in route["path"] else "binary_input"
            )
        if "send_bytes" in module_source:
            server.add("binary_output")
    return {
        "routeKey": route["routeKey"],
        "clientMessageClasses": sorted(client),
        "serverMessageClasses": sorted(server),
        "unknownMessageBehavior": "fail_closed",
        "maxFrameBytes": 1_048_576,
    }


def _sse_protocol(route: dict[str, Any]) -> dict[str, Any]:
    source = str(route.pop("handlerSource", ""))
    normalized = source.lower()
    upstream_cursor = (
        "snapshot_then_live_tail_no_server_replay"
        if "does not replay history" in normalized
        else "unspecified_fail_closed"
    )
    return {
        "routeKey": route["routeKey"],
        "accept": "text/event-stream",
        "upstreamCursorBehavior": upstream_cursor,
        "facadeCursorBehavior": "durable_sequence_cursor_and_last_event_id",
        "reconnectAuthorization": "reauthorize_on_connect_and_periodically",
    }


def _ui_route_references(repo_root: Path) -> list[dict[str, str]]:
    references: set[tuple[str, str]] = set()
    for path in _ui_files(repo_root / _UI_ROOT):
        source = path.read_text(encoding="utf-8")
        for match in _UI_ROUTE_LITERAL.finditer(source):
            value = match.group(1).split("?", 1)[0]
            value = re.sub(r"\$\{[^}]+\}", "{parameter}", value)
            references.add((value, path.relative_to(repo_root).as_posix()))
    return [
        {"path": path, "sourceFile": source_file}
        for path, source_file in sorted(references)
    ]


def _artifact_digests(repo_root: Path) -> dict[str, str]:
    omnigent = repo_root / "omnigent"
    server_files = _python_files(omnigent / "omnigent/server")
    host_files = _python_files(omnigent / "omnigent/host")
    harness_files = _python_files(omnigent / "omnigent/runtime/harnesses") + _python_files(
        repo_root / "moonmind/omnigent/harness_platform"
    )
    facade_files = [
        repo_root / "moonmind/omnigent/native_ui_compat.py",
        repo_root / "moonmind/omnigent/native_ui_route_inventory.py",
        repo_root / "moonmind/omnigent/workflow_chat_facade.py",
        repo_root / "moonmind/omnigent/effective_capabilities.py",
        repo_root / "moonmind/omnigent/bridge_artifacts.py",
        repo_root / "api_service/api/routers/omnigent_bridge.py",
    ]
    return {
        "omnigent": "git:" + _git_revision(omnigent),
        "ui": _file_digest(repo_root, _ui_files(omnigent / "web/src")),
        "server": _file_digest(repo_root, server_files),
        "host": _file_digest(repo_root, host_files),
        "harnessImplementation": _file_digest(repo_root, harness_files),
        "moonmindFacade": _file_digest(repo_root, facade_files),
    }


def generate_native_ui_route_inventory(repo_root: str | Path) -> dict[str, Any]:
    """Generate the deterministic exact-stock inventory for ``repo_root``."""

    root = Path(repo_root).resolve()
    stock_routes = _stock_routes(root)
    websocket_protocols = [
        _websocket_protocol(route.copy(), repo_root=root)
        for route in stock_routes
        if route["transport"] == TRANSPORT_WEBSOCKET
    ]
    sse_protocols = [
        _sse_protocol(route.copy())
        for route in stock_routes
        if route["transport"] == TRANSPORT_SSE
    ]
    classified = [_classify_route(route) for route in stock_routes]
    body: dict[str, Any] = {
        "schemaVersion": INVENTORY_SCHEMA_VERSION,
        "sourceIssue": "MoonLadderStudios/MoonMind#3635",
        "artifactDigests": _artifact_digests(root),
        "routes": classified,
        "sseProtocols": sse_protocols,
        "uiRouteReferences": _ui_route_references(root),
        "websocketProtocols": websocket_protocols,
        "routeCount": len(classified),
        "classifiedRouteCount": len(classified),
        "unclassifiedRouteCount": 0,
    }
    body["inventoryDigest"] = _canonical_digest(body)
    return body


__all__ = [
    "INVENTORY_SCHEMA_VERSION",
    "NativeUiRouteInventoryError",
    "generate_native_ui_route_inventory",
]
