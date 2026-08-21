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

INVENTORY_SCHEMA_VERSION = "moonmind.omnigent.native-ui-route-inventory/v4"

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_ROUTE_METHODS = _HTTP_METHODS | {"websocket"}
_PARAMETER = re.compile(r"\{(?P<name>[^}:]+)(?::[^}]+)?\}")
_UI_NETWORK_CALL = re.compile(
    r"(?<![A-Za-z0-9_])(?P<constructor>new\s+)?"
    r"(?P<name>authenticatedFetch|hostFetch|fetch|resolveWebSocketUrl|WebSocket)\s*\("
)
_UI_CONST_ASSIGNMENT = re.compile(
    r"(?:const|let)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*="
)
_UI_FUNCTION = re.compile(
    r"(?:export\s+)?function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\([^)]*\)"
    r"(?:\s*:\s*[A-Za-z0-9_$<>|\[\] ]+)?\s*\{"
)
_TYPE_COMPARISON = re.compile(
    r"(?:msg|control)\.get\([\"']type[\"']\)\s*(?:==|!=)\s*[\"']([^\"']+)"
)
_UI_ROUTE_LITERAL = re.compile(
    r'''(?P<quote>["'`])'''
    r'''(?P<body>/(?:v1(?:/|\\/)|health)(?:\\.|(?!\1).)*?)'''
    r'''(?P=quote)'''
)

_SERVER_ROUTE_ROOT = Path("omnigent/omnigent/server/routes")
_SERVER_APP = Path("omnigent/omnigent/server/app.py")
_UI_ROOT = Path("omnigent/web/src")
_FACADE_ARTIFACT_PATHS = (
    "moonmind/omnigent/native_ui_compat.py",
    "moonmind/omnigent/native_ui_route_inventory.py",
    "moonmind/omnigent/workflow_chat_facade.py",
    "moonmind/omnigent/effective_capabilities.py",
    "moonmind/omnigent/bridge_artifacts.py",
    "moonmind/omnigent/bridge_config.py",
    "moonmind/omnigent/bridge_embedded.py",
    "moonmind/omnigent/bridge_store.py",
    "moonmind/workflows/adapters/omnigent_client.py",
    "api_service/api/routers/omnigent_bridge.py",
)


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


def exact_artifact_role_digest(root: str | Path, role: str) -> str:
    """Digest one role's exact in-image files using inventory semantics."""

    resolved = Path(root).resolve()
    if role == "omnigent_host":
        paths = _python_files(resolved / "omnigent/omnigent/host")
    elif role == "moonmind_facade":
        paths = [resolved / path for path in _FACADE_ARTIFACT_PATHS]
    elif role == "moonmind_harness":
        paths = _python_files(resolved / "moonmind/omnigent/harness_platform")
    else:
        raise NativeUiRouteInventoryError(f"unknown exact artifact role: {role}")
    return _file_digest(resolved, paths)


def _ui_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs"}
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
            status_codes = [
                int(keyword.value.value)
                for keyword in decorator.keywords
                if keyword.arg == "status_code"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, int)
            ]
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
                    "handlerDigest": "sha256:"
                    + sha256(handler_source.encode()).hexdigest(),
                    "declaredStatusCodes": status_codes or [200],
                    "declaredResponseModel": next(
                        (
                            ast.unparse(keyword.value)
                            for keyword in decorator.keywords
                            if keyword.arg == "response_model"
                        ),
                        None,
                    ),
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
        route["sourceFile"] = (
            Path(route["sourceFile"]).relative_to(repo_root).as_posix()
        )
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
        if name == "environment_id":
            # The scoped facade intentionally exposes only the stock UI's
            # build-time default environment.  Sampling another value would
            # classify the whole parameterized server declaration fail-closed,
            # even though its literal ``default`` variant is served.  The
            # resulting inventory entry carries the constraint explicitly so
            # every other environment id still fails closed.
            return "default"
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
    path_constraints: dict[str, Any] = {}
    if matched is not None and "{environment_id}" in route["path"]:
        path_constraints["environment_id"] = {
            "allowedLiterals": ["default"],
            "otherValuesBehavior": CODE_TRANSPORT_UNSUPPORTED,
        }
    return {
        key: value
        for key, value in {
            "routeKey": route["routeKey"],
            "method": route["method"],
            "path": route["path"],
            "transport": route["transport"],
            "sourceFile": route["sourceFile"],
            "sourceLine": route["sourceLine"],
            "handlerDigest": route["handlerDigest"],
            "classification": classification,
            "facadeOperation": facade_operation,
            "pathConstraints": path_constraints,
            **policy,
            "responseContract": {
                "declaredStatusCodes": list(route["declaredStatusCodes"]),
                "declaredResponseModel": route["declaredResponseModel"],
                "facadeBody": (
                    "bounded_virtualized_payload"
                    if classification == "binding_scoped"
                    else "stable_fail_closed_diagnostic"
                ),
                "mutationReceiptSchemaVersion": (
                    "moonmind.omnigent.mutation-receipt.v1"
                    if route["method"] not in {"GET", "WEBSOCKET"}
                    and classification == "binding_scoped"
                    else None
                ),
            },
        }.items()
    }


def _strip_javascript_comments(source: str) -> str:
    """Blank JS comments while preserving strings, templates, and line offsets."""

    result = list(source)
    index = 0
    quote: str | None = None
    escaped = False
    template_expression_depth = 0
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote == "`" and char == "$" and following == "{":
                template_expression_depth += 1
            elif quote == "`" and template_expression_depth and char == "}":
                template_expression_depth -= 1
            elif char == quote and template_expression_depth == 0:
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == "/" and following == "/":
            end = source.find("\n", index + 2)
            end = len(source) if end < 0 else end
            for position in range(index, end):
                result[position] = " "
            index = end
            continue
        if char == "/" and following == "*":
            end = source.find("*/", index + 2)
            end = len(source) - 2 if end < 0 else end
            for position in range(index, min(end + 2, len(source))):
                if result[position] != "\n":
                    result[position] = " "
            index = end + 2
            continue
        index += 1
    return "".join(result)


def _javascript_call(source: str, open_parenthesis: int) -> tuple[str, str] | None:
    """Return a call's first argument and full bounded expression."""

    depth = 0
    quote: str | None = None
    escaped = False
    template_expression_depth = 0
    first_comma: int | None = None
    for index in range(open_parenthesis + 1, len(source)):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote == "`" and char == "$" and following == "{":
                template_expression_depth += 1
            elif quote == "`" and template_expression_depth and char == "}":
                template_expression_depth -= 1
            elif char == quote and template_expression_depth == 0:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            if char == ")" and depth == 0:
                argument_end = first_comma if first_comma is not None else index
                return (
                    source[open_parenthesis + 1 : argument_end].strip(),
                    source[open_parenthesis + 1 : index],
                )
            depth = max(0, depth - 1)
        elif char == "," and depth == 0 and first_comma is None:
            first_comma = index
    return None


def _parameter_name(expression: str) -> str:
    identifiers = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", expression)
    ignored = {
        "encodeURIComponent",
        "String",
        "toString",
        "trim",
        "replace",
        "pathname",
    }
    selected = next(
        (item for item in reversed(identifiers) if item not in ignored),
        "parameter",
    )
    return re.sub(r"(?<!^)(?=[A-Z])", "_", selected).lower()


def _javascript_string_literal(value: str) -> str | None:
    """Return one plain JavaScript string literal's value."""

    candidate = value.strip()
    if len(candidate) < 2 or candidate[0] not in {"'", '"', "`"}:
        return None
    if candidate[-1] != candidate[0] or "${" in candidate:
        return None
    try:
        # JSON handles double-quoted literals.  ``ast.literal_eval`` covers the
        # single-quoted form used by TypeScript without executing source.
        if candidate[0] == '"':
            parsed = json.loads(candidate)
        elif candidate[0] == "'":
            parsed = ast.literal_eval(candidate)
        else:
            parsed = candidate[1:-1]
    except (ValueError, SyntaxError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, str) else None


def _template_route_text(
    value: str, *, constants: dict[str, str] | None = None
) -> str | None:
    quote = value[0]
    if len(value) < 2 or quote not in {"'", '"', "`"} or value[-1] != quote:
        return None
    raw = value[1:-1]
    output: list[str] = []
    cursor = 0
    while cursor < len(raw):
        start = raw.find("${", cursor)
        if start < 0:
            output.append(raw[cursor:])
            break
        output.append(raw[cursor:start])
        depth = 1
        index = start + 2
        nested_quote: str | None = None
        escaped = False
        while index < len(raw) and depth:
            char = raw[index]
            if nested_quote is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == nested_quote:
                    nested_quote = None
            elif char in {"'", '"', "`"}:
                nested_quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
        if depth:
            return None
        interpolation = raw[start + 2 : index - 1].strip()
        if constants and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", interpolation):
            literal = constants.get(interpolation)
            if literal is not None:
                output.append(literal)
                cursor = index
                continue
        name = _parameter_name(interpolation)
        # Optional suffix templates describe two exact stock routes: the base
        # collection and its path-addressed variant.  The path variant is the
        # stronger normalized join; the base is independently present at other
        # call sites and in the server declaration inventory.
        if re.search(r"\?\s*`/", interpolation):
            output.append("/{" + name + ":path}")
        elif (
            name in {"query", "params", "search_params", "after"}
            or re.search(r"\?\s*`\?", interpolation)
        ):
            pass
        else:
            output.append("{" + name + "}")
        cursor = index
    return "".join(output).replace("\\/", "/")


def _javascript_route_value(
    expression: str, *, constants: dict[str, str] | None = None
) -> str | None:
    """Resolve a route expression made only from string/template literals."""

    value = expression.strip()
    parts: list[str] = []
    cursor = 0
    while cursor < len(value):
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        if cursor >= len(value) or value[cursor] not in {"'", '"', "`"}:
            return None
        quote = value[cursor]
        start = cursor
        cursor += 1
        escaped = False
        template_depth = 0
        while cursor < len(value):
            char = value[cursor]
            following = value[cursor + 1] if cursor + 1 < len(value) else ""
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote == "`" and char == "$" and following == "{":
                template_depth += 1
                cursor += 1
            elif quote == "`" and template_depth and char == "}":
                template_depth -= 1
            elif char == quote and template_depth == 0:
                cursor += 1
                break
            cursor += 1
        else:
            return None
        part = _template_route_text(value[start:cursor], constants=constants)
        if part is None:
            return None
        parts.append(part)
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        if cursor == len(value):
            break
        if value[cursor] != "+":
            return None
        cursor += 1
    raw = "".join(parts).split("?", 1)[0]
    return raw if raw.startswith("/") else None


def _javascript_statement(source: str, start: int) -> str:
    """Read one assignment expression through its top-level semicolon."""

    quote: str | None = None
    escaped = False
    template_depth = 0
    for index in range(start, len(source)):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote == "`" and char == "$" and following == "{":
                template_depth += 1
            elif quote == "`" and template_depth and char == "}":
                template_depth -= 1
            elif char == quote and template_depth == 0:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == ";":
            return source[start:index].strip()
    return ""


def _javascript_block(source: str, open_brace: int) -> str:
    """Return one balanced function block, preserving nested literals."""

    depth = 0
    quote: str | None = None
    escaped = False
    template_depth = 0
    for index in range(open_brace + 1, len(source)):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote == "`" and char == "$" and following == "{":
                template_depth += 1
            elif quote == "`" and template_depth and char == "}":
                template_depth -= 1
            elif char == quote and template_depth == 0:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            if depth == 0:
                return source[open_brace + 1 : index]
            depth -= 1
    return ""


def _function_route_values(
    source: str, *, constants: dict[str, str] | None = None
) -> dict[str, str]:
    """Resolve route-only builder functions used as network-call arguments."""

    functions: dict[str, str] = {}
    for function in _UI_FUNCTION.finditer(source):
        body = _javascript_block(source, function.end() - 1)
        values = {
            route
            for assignment in _UI_CONST_ASSIGNMENT.finditer(body)
            if (expression := _javascript_statement(body, assignment.end()))
            and (
                route := _javascript_route_value(
                    expression,
                    constants=constants,
                )
            )
            is not None
        }
        if len(values) == 1:
            functions[function.group("name")] = values.pop()
    return functions


def _stock_route_for_ui_reference(
    *, method: str, path: str, routes: list[dict[str, Any]]
) -> dict[str, Any] | None:
    for route in routes:
        if route["method"] != method:
            continue
        fragments: list[str] = []
        cursor = 0
        for parameter in _PARAMETER.finditer(route["path"]):
            fragments.append(re.escape(route["path"][cursor : parameter.start()]))
            is_path = ":path" in parameter.group(0)
            fragments.append(
                r"(?:\{[a-z0-9_]+\}|.+)"
                if is_path
                else r"(?:\{[a-z0-9_]+\}|[^/]+)"
            )
            cursor = parameter.end()
        fragments.append(re.escape(route["path"][cursor:]))
        if re.fullmatch("".join(fragments), path):
            return route
    return None


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
        if (
            '"type": "resize"' in module_source
            or '"type") == "resize"' in module_source
        ):
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


def _ui_route_references(
    repo_root: Path, *, stock_routes: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract method-aware network calls and join each to one stock route.

    This is intentionally call-site based: comments, documentation examples,
    and arbitrary route-looking strings are not network evidence.  Dynamic
    template parameters are normalized before joining to the exact FastAPI
    declarations. A resolved call that cannot be joined is retained as an
    explicit fail-closed reference. Calls whose path is supplied through a
    component or transport-wrapper argument are separately retained as
    delegated calls; their runtime owner is the scoped host-fetch adapter
    followed by the same exact facade matcher.
    """

    references: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    delegated: dict[tuple[str, int, str], dict[str, Any]] = {}
    for path in _ui_files(repo_root / _UI_ROOT):
        source = _strip_javascript_comments(path.read_text(encoding="utf-8"))
        assignments = {
            match.group("name"): expression
            for match in _UI_CONST_ASSIGNMENT.finditer(source)
            if (expression := _javascript_statement(source, match.end()))
        }
        literal_constants = {
            name: literal
            for name, expression in assignments.items()
            if (literal := _javascript_string_literal(expression)) is not None
        }
        route_constants = {
            name: route
            for name, expression in assignments.items()
            if (
                route := _javascript_route_value(
                    expression,
                    constants=literal_constants,
                )
            )
            is not None
        }
        function_routes = _function_route_values(
            source,
            constants=literal_constants,
        )
        for match in _UI_NETWORK_CALL.finditer(source):
            name = match.group("name")
            if name == "WebSocket" and not match.group("constructor"):
                continue
            call = _javascript_call(source, match.end() - 1)
            if call is None:
                continue
            first_argument, full_call = call
            method_match = re.search(
                r"\bmethod\s*:\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']",
                full_call,
                re.IGNORECASE,
            )
            method = (
                "WEBSOCKET"
                if name in {"WebSocket", "resolveWebSocketUrl"}
                else method_match.group(1).upper()
                if method_match
                else "GET"
            )
            route_path = _javascript_route_value(
                first_argument,
                constants=literal_constants,
            )
            if route_path is None and re.fullmatch(
                r"[A-Za-z_$][A-Za-z0-9_$]*", first_argument
            ):
                route_path = route_constants.get(first_argument)
            if route_path is None:
                function_call = re.fullmatch(
                    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(.*\)",
                    first_argument,
                    re.DOTALL,
                )
                if function_call:
                    route_path = function_routes.get(function_call.group("name"))
            if route_path is None:
                source_file = path.relative_to(repo_root).as_posix()
                source_line = source.count("\n", 0, match.start()) + 1
                parameter = _parameter_name(first_argument)
                classification = (
                    "stable_unsupported_route"
                    if first_argument.lstrip().startswith(
                        ('"/auth', "'/auth", "`/auth")
                    )
                    else "scoped_transport_adapter"
                )
                delegated[(source_file, source_line, name)] = {
                    "networkApi": name,
                    "method": method,
                    "pathPattern": f"/{{{parameter}:path}}",
                    "sourceFile": source_file,
                    "sourceLine": source_line,
                    "classification": classification,
                    "resolution": (
                        "reject_before_upstream"
                        if classification == "stable_unsupported_route"
                        else "resolve_at_call_site_then_exact_route_gate"
                    ),
                    "argumentDigest": "sha256:"
                    + sha256(first_argument.encode()).hexdigest(),
                    "unknownBehavior": CODE_TRANSPORT_UNSUPPORTED,
                }
                continue
            if not route_path.startswith(("/v1/", "/health")):
                source_file = path.relative_to(repo_root).as_posix()
                source_line = source.count("\n", 0, match.start()) + 1
                delegated[(source_file, source_line, name)] = {
                    "networkApi": name,
                    "method": method,
                    "pathPattern": route_path,
                    "sourceFile": source_file,
                    "sourceLine": source_line,
                    "classification": "stable_unsupported_route",
                    "resolution": "reject_before_upstream",
                    "argumentDigest": "sha256:"
                    + sha256(first_argument.encode()).hexdigest(),
                    "unknownBehavior": CODE_TRANSPORT_UNSUPPORTED,
                }
                continue
            matched_route = _stock_route_for_ui_reference(
                method=method,
                path=route_path,
                routes=stock_routes,
            )
            classified = _classify_route(matched_route) if matched_route else None
            source_file = path.relative_to(repo_root).as_posix()
            source_line = source.count("\n", 0, match.start()) + 1
            key = (method, route_path, source_file, source_line)
            references[key] = {
                "routeKey": (
                    matched_route["routeKey"]
                    if matched_route
                    else f"{method} {route_path}"
                ),
                "method": method,
                "networkApi": name,
                "path": route_path,
                "sourceFile": source_file,
                "sourceLine": source_line,
                "join": "exact_stock_route" if matched_route else "fail_closed",
                "classification": (
                    classified["classification"] if classified else "fail_closed"
                ),
                "facadeOperation": (
                    classified["facadeOperation"] if classified else None
                ),
                "unsupportedBehavior": (
                    classified["unsupportedBehavior"]
                    if classified
                    else CODE_TRANSPORT_UNSUPPORTED
                ),
            }
    reference_values = [references[key] for key in sorted(references)]
    for call in delegated.values():
        if call["classification"] != "scoped_transport_adapter":
            continue
        candidates = [
            reference
            for reference in reference_values
            if (
                reference["networkApi"] == call["networkApi"]
                or call["method"] == reference["method"] == "WEBSOCKET"
            )
        ]
        resolved_routes = [
            {
                "method": reference["method"],
                "path": reference["path"],
                "routeKey": reference["routeKey"],
            }
            for reference in candidates
        ]
        if not resolved_routes:
            # A dynamic adapter with no statically resolved caller has no
            # authority to reach upstream merely because its argument happens
            # to look like a path at runtime.
            call["classification"] = "stable_unsupported_route"
            call["resolution"] = "reject_before_upstream"
        else:
            call["resolution"] = "exact_method_path_allowlist"
            call["resolvedRoutes"] = resolved_routes
    return (
        reference_values,
        [delegated[key] for key in sorted(delegated)],
    )


def _compiled_ui_route_observations(
    compiled_root: Path,
    *,
    stock_routes: list[dict[str, Any]],
    source_references: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify route literals observed in the exact deployed UI bundle.

    Production bundling renames local variables and transport wrappers, so the
    compiled bytes cannot reliably preserve a method beside every route
    literal.  Each literal is therefore joined back to the method-aware source
    call sites from the same pinned server image through the independently
    parsed stock route key.  A literal with no such join remains explicit
    fail-closed evidence instead of gaining upstream authority by resemblance.
    """

    joined_source_keys = {
        str(reference.get("routeKey"))
        for reference in source_references
        if reference.get("join") == "exact_stock_route"
    }
    observations: dict[tuple[str, str], dict[str, Any]] = {}
    for path in _ui_files(compiled_root):
        source = _strip_javascript_comments(path.read_text(encoding="utf-8"))
        for match in _UI_ROUTE_LITERAL.finditer(source):
            literal = match.group("quote") + match.group("body") + match.group("quote")
            route_path = _template_route_text(literal)
            if route_path is None:
                continue
            route_path = route_path.split("?", 1)[0].replace("\\/", "/")
            allowlist: list[dict[str, str]] = []
            for route in stock_routes:
                if route["routeKey"] not in joined_source_keys:
                    continue
                matched = _stock_route_for_ui_reference(
                    method=route["method"],
                    path=route_path,
                    routes=[route],
                )
                if matched is not None:
                    allowlist.append(
                        {
                            "method": route["method"],
                            "path": route_path,
                            "routeKey": route["routeKey"],
                        }
                    )
            source_file = path.relative_to(compiled_root).as_posix()
            key = (source_file, route_path)
            observations[key] = {
                "pathPattern": route_path,
                "sourceFile": source_file,
                "literalDigest": "sha256:"
                + sha256(match.group(0).encode()).hexdigest(),
                "classification": (
                    "scoped_transport_adapter"
                    if allowlist
                    else "stable_unsupported_route"
                ),
                "resolution": (
                    "exact_method_path_allowlist"
                    if allowlist
                    else "reject_before_upstream"
                ),
                **({"resolvedRoutes": allowlist} if allowlist else {}),
                "unknownBehavior": CODE_TRANSPORT_UNSUPPORTED,
            }
    route_literals = [observations[key] for key in sorted(observations)]
    if not route_literals:
        raise NativeUiRouteInventoryError(
            "compiled stock UI contains no observable route literals"
        )
    body = {
        "routeLiterals": route_literals,
        "routeLiteralCount": len(route_literals),
        "classifiedRouteLiteralCount": len(route_literals),
    }
    body["digest"] = _canonical_digest(body)
    return body


def _artifact_digests(
    repo_root: Path,
    *,
    facade_root: Path,
    omnigent_revision: str | None,
) -> dict[str, str]:
    omnigent = repo_root / "omnigent"
    server_files = _python_files(omnigent / "omnigent/server")
    stock_harness_files = _python_files(omnigent / "omnigent/runtime/harnesses")
    moonmind_harness_digest = exact_artifact_role_digest(
        facade_root, "moonmind_harness"
    )
    # Namespace each half before combining so checkout generation and in-image
    # generation produce the same digest despite using different filesystem
    # roots for the MoonMind classifier.
    harness_digest = _canonical_digest(
        {
            "omnigent": _file_digest(repo_root, stock_harness_files),
            "moonmind": moonmind_harness_digest,
        }
    )
    revision = omnigent_revision or _git_revision(omnigent)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise NativeUiRouteInventoryError("pinned Omnigent revision is invalid")
    return {
        "omnigent": "git:" + revision,
        "ui": _file_digest(repo_root, _ui_files(omnigent / "web/src")),
        "server": _file_digest(repo_root, server_files),
        "host": exact_artifact_role_digest(repo_root, "omnigent_host"),
        "harnessImplementation": harness_digest,
        "moonmindFacade": exact_artifact_role_digest(
            facade_root, "moonmind_facade"
        ),
    }


def generate_native_ui_route_inventory(
    repo_root: str | Path,
    *,
    facade_root: str | Path | None = None,
    omnigent_revision: str | None = None,
    compiled_ui_root: str | Path | None = None,
    deployable_images: dict[str, str] | None = None,
    observed_artifact_digests: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Generate a deterministic inventory from checkout or in-image bytes.

    The reviewed fixture uses the checkout mode for human review.  The required
    exact-artifact gate supplies ``compiled_ui_root`` and all three immutable
    deployable image identities while running this generator *inside* the
    pinned stock server image.  Keeping those modes explicit prevents the CI
    driver from silently substituting checkout sources for deployed bytes.
    """

    root = Path(repo_root).resolve()
    facade = Path(facade_root or root).resolve()
    compiled_root = (
        Path(compiled_ui_root).resolve() if compiled_ui_root is not None else None
    )
    image_refs = dict(deployable_images or {})
    observed_digests = dict(observed_artifact_digests or {})
    running_image_mode = compiled_root is not None or bool(image_refs)
    if running_image_mode:
        required_roles = {"omnigentServer", "omnigentHost", "moonmindFacade"}
        if set(image_refs) != required_roles:
            raise NativeUiRouteInventoryError(
                "in-image inventory requires exact server, host, and facade images"
            )
        if compiled_root is None or not compiled_root.is_dir():
            raise NativeUiRouteInventoryError(
                "in-image inventory requires the compiled stock UI directory"
            )
        for role, image_ref in image_refs.items():
            if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image_ref):
                raise NativeUiRouteInventoryError(
                    f"{role} image must be digest-pinned"
                )
        expected_observed_roles = {
            "omnigentHost",
            "moonmindFacade",
            "moonmindHarness",
        }
        if set(observed_digests) != expected_observed_roles:
            raise NativeUiRouteInventoryError(
                "in-image inventory requires host, facade, and harness byte "
                "attestations"
            )
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
    ui_references, ui_delegated_calls = _ui_route_references(
        root,
        stock_routes=stock_routes,
    )
    artifact_digests = _artifact_digests(
        root,
        facade_root=facade,
        omnigent_revision=omnigent_revision,
    )
    if running_image_mode:
        expected_observed = {
            "omnigentHost": artifact_digests["host"],
            "moonmindFacade": artifact_digests["moonmindFacade"],
            "moonmindHarness": exact_artifact_role_digest(
                facade, "moonmind_harness"
            ),
        }
        if observed_digests != expected_observed:
            raise NativeUiRouteInventoryError(
                "in-image source bytes do not match the classified artifacts"
            )
    compiled_ui_observations = (
        _compiled_ui_route_observations(
            compiled_root,
            stock_routes=stock_routes,
            source_references=ui_references,
        )
        if compiled_root is not None
        else None
    )
    body: dict[str, Any] = {
        "schemaVersion": INVENTORY_SCHEMA_VERSION,
        "sourceIssue": "MoonLadderStudios/MoonMind#3635",
        "artifactDigests": artifact_digests,
        "artifactProvenance": {
            "sourceMode": "running_images" if running_image_mode else "checkout",
            "generationBoundary": (
                "inside_pinned_omnigent_server_image"
                if running_image_mode
                else "reviewed_checkout_fixture"
            ),
            "compiledUiDigest": (
                _file_digest(compiled_root, compiled_root.rglob("*"))
                if compiled_root is not None
                else None
            ),
            "deployableImages": image_refs,
            "inImageArtifactDigests": observed_digests,
            "compiledUiNetworkSurface": compiled_ui_observations,
        },
        "routes": classified,
        "sseProtocols": sse_protocols,
        "uiDelegatedNetworkCalls": ui_delegated_calls,
        "uiRouteReferences": ui_references,
        "websocketProtocols": websocket_protocols,
        "routeCount": len(classified),
        "classifiedRouteCount": len(classified),
        "unclassifiedRouteCount": 0,
    }
    body["uiDelegatedCallCount"] = len(body["uiDelegatedNetworkCalls"])
    body["uiReferenceCount"] = len(body["uiRouteReferences"])
    classification_body = {
        key: value for key, value in body.items() if key != "artifactProvenance"
    }
    body["classificationDigest"] = _canonical_digest(classification_body)
    body["inventoryDigest"] = _canonical_digest(body)
    return body


__all__ = [
    "INVENTORY_SCHEMA_VERSION",
    "NativeUiRouteInventoryError",
    "exact_artifact_role_digest",
    "generate_native_ui_route_inventory",
]
