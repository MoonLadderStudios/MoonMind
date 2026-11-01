"""Codex Profile configuration shared by direct and rendered launches.

This module is stdlib-only so the same serializer and private-home adjustment
can be packaged into a host without importing MoonMind or its database.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import shlex
import sys
import tempfile
import tomllib
from pathlib import Path

_CONFIG_PATH = "{{runtime_support_dir}}/codex-home/config.toml"
_CODEX_HOME = "{{runtime_support_dir}}/codex-home"
MINIMAX_MODEL = "codex-MiniMax-M2.7"


def minimax_file_templates(*, legacy=False):
    document = {
        "model_providers": {
            "minimax": {
                "name": (
                    "MiniMax Chat Completions API"
                    if legacy
                    else "MiniMax Responses API"
                ),
                "base_url": "https://api.minimax.io/v1",
                "env_key": "MINIMAX_API_KEY",
                "wire_api": "chat" if legacy else "responses",
                "requires_openai_auth": False,
                "request_max_retries": 4,
                "stream_max_retries": 10,
                "stream_idle_timeout_ms": 300000,
            }
        },
        "profiles": {"m27": {"model": MINIMAX_MODEL, "model_provider": "minimax"}},
    }
    if legacy:
        document = {"profile": "m27", **document}
    else:
        document = {"model": MINIMAX_MODEL, "model_provider": "minimax", **document}
    return [
        {
            "path": _CONFIG_PATH,
            "format": "toml",
            "merge_strategy": "deep_merge",
            "content_template": document,
            "permissions": "0600",
        }
    ]


def _template(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=False, mode="json")
    if not isinstance(value, dict):
        raise ValueError("Codex file template must be an object")
    return {
        "path": value.get("path"),
        "format": str(value.get("format", "text")).lower(),
        "merge_strategy": value.get(
            "merge_strategy", value.get("mergeStrategy", "replace")
        ),
        "content_template": value.get(
            "content_template", value.get("contentTemplate", "")
        ),
        "permissions": (
            format(value["permissions"], "04o")
            if type(value.get("permissions")) is int
            else str(value.get("permissions") or "0600")
        ),
    }


def repaired_managed_templates(profile):
    """Repair only the unchanged obsolete managed recipe, without editing it.

    Existing rows and recorded snapshots keep their original bytes. Rendering
    the recognized stock recipe uses the supported Responses wire and explicit
    provider/model selection. Operator-authored content is never coerced.
    """
    templates = [
        _template(item) for item in getattr(profile, "file_templates", None) or []
    ]
    expected = {
        "profile_id": "codex_minimax_m27",
        "runtime_id": "codex_cli",
        "provider_id": "minimax",
        "default_model": MINIMAX_MODEL,
        "model_overrides": {"codex_profile_name": "m27"},
        "secret_refs": {"provider_api_key": "env://MINIMAX_API_KEY"},
        "env_template": {"MINIMAX_API_KEY": {"from_secret_ref": "provider_api_key"}},
        "home_path_overrides": {"CODEX_HOME": _CODEX_HOME},
        "command_behavior": {"suppress_default_model_flag": True},
    }
    if templates == minimax_file_templates(legacy=True) and all(
        getattr(profile, key, None) == value for key, value in expected.items()
    ):
        return minimax_file_templates(), True
    return templates, False


def _merge(base, overlay):
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        result[key] = (
            _merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else copy.deepcopy(value)
        )
    return result


def _has_authority(value):
    if isinstance(value, dict):
        forbidden = {
            "api_key",
            "token",
            "secret",
            "password",
            "bearer_token",
            "experimental_bearer_token",
            "from_secret_ref",
            "authorization",
            "x-api-key",
        }
        return any(
            str(key).lower() in forbidden or _has_authority(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_authority(item) for item in value)
    return isinstance(value, str) and ("{{" in value or "\0" in value)


def configuration_for_profile(profile):
    """Return one declared Codex config and its selected key role, or a reason."""
    try:
        templates, repaired = repaired_managed_templates(profile)
        home = dict(getattr(profile, "home_path_overrides", None) or {})
        if set(home) != {"CODEX_HOME"}:
            return None, "Codex file templates require one declared CODEX_HOME"
        home_path = str(home["CODEX_HOME"])
        if (
            not home_path.startswith("{{runtime_support_dir}}/")
            or ".." in Path(home_path).parts
        ):
            return None, "Codex HOME must be within its run support directory"
        document = {}
        if not templates:
            return None, "Codex file templates are empty"
        for template in templates:
            if (
                template["path"] != home_path.rstrip("/") + "/config.toml"
                or template["format"] != "toml"
            ):
                return (
                    None,
                    "Codex file templates must target the declared HOME config.toml",
                )
            if int(template["permissions"], 8) != 0o600:
                return None, "Codex configuration requires private file permissions"
            content = template["content_template"]
            if isinstance(content, str):
                content = tomllib.loads(content)
            if not isinstance(content, dict) or _has_authority(content):
                return (
                    None,
                    "Codex config must use a declared environment SecretRef for authentication",
                )
            strategy = template["merge_strategy"]
            if strategy == "replace":
                document = copy.deepcopy(content)
            elif strategy == "deep_merge":
                document = _merge(document, content)
            elif strategy == "append":
                document = tomllib.loads(render_toml(document) + render_toml(content))
            else:
                return None, "Codex file template merge strategy is unsupported"
        selector = (getattr(profile, "model_overrides", None) or {}).get(
            "codex_profile_name"
        ) or document.get("profile")
        if selector:
            layer = document.get("profiles", {}).get(selector)
            if not isinstance(layer, dict):
                return None, "the selected Codex config profile is unavailable"
            document = _merge(document, layer)
        document.pop("profile", None)
        document.pop("profiles", None)
        supported_settings = {
            "model",
            "model_provider",
            "model_providers",
            "model_reasoning_effort",
            "model_reasoning_summary",
            "model_verbosity",
            "model_context_window",
            "model_auto_compact_token_limit",
            "personality",
        }
        if set(document) - supported_settings:
            return (
                None,
                "Codex configuration includes settings outside the shared model configuration",
            )
        provider_name = document.get("model_provider")
        providers = document.get("model_providers")
        if not isinstance(providers, dict) or set(providers) != {provider_name}:
            return None, "Codex file templates must name one selected model provider"
        provider = providers[provider_name]
        if (
            not isinstance(provider, dict)
            or provider.get("wire_api", "responses") != "responses"
        ):
            return (
                None,
                "the installed Codex requires Responses wire; update this authored configuration",
            )
        supported_provider = {
            "name",
            "base_url",
            "env_key",
            "env_key_instructions",
            "wire_api",
            "requires_openai_auth",
            "request_max_retries",
            "stream_max_retries",
            "stream_idle_timeout_ms",
            "supports_websockets",
        }
        if (
            set(provider) - supported_provider
            or provider.get("requires_openai_auth", False) is not False
        ):
            return (
                None,
                "Codex provider authentication cannot be carried by the selected materializer",
            )
        key_name = provider.get("env_key")
        environment = dict(getattr(profile, "env_template", None) or {})
        if (
            not isinstance(key_name, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_name)
            or set(environment) != {key_name}
        ):
            return None, "Codex config requires its exact declared key environment"
        value = environment[key_name]
        if not isinstance(value, dict) or set(value) != {"from_secret_ref"}:
            return None, "Codex config key must come from a SecretRef"
        role = value["from_secret_ref"]
        if not isinstance(role, str) or not (
            getattr(profile, "secret_refs", None) or {}
        ).get(role):
            return None, "Codex config key has no SecretRef"
        for key in (
            "request_max_retries",
            "stream_max_retries",
            "stream_idle_timeout_ms",
        ):
            if key in provider and (
                type(provider[key]) is not int or provider[key] < 0
            ):
                return None, f"Codex provider {key} must be a nonnegative integer"
        # Validate serializability before this becomes a runtime handle.
        render_toml(document)
        return {
            "document": document,
            "provider": copy.deepcopy(provider),
            "providerName": provider_name,
            "secretRole": role,
            "managedRecipeRepaired": repaired,
        }, ""
    except (TypeError, ValueError, KeyError):
        return None, "Codex file templates contain invalid configuration"


def _key(value):
    value = str(value)
    return value if re.fullmatch(r"[A-Za-z0-9_-]+", value) else json.dumps(value)


def _scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) or isinstance(value, float) and math.isfinite(value):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(f"{_key(key)} = {_scalar(item)}" for key, item in value.items())
            + "}"
        )
    raise ValueError("unsupported TOML value")


def render_toml(document):
    if not isinstance(document, dict):
        raise ValueError("TOML file_templates require object content_template")
    lines = []

    def table(path, value):
        if path:
            lines.append("[" + ".".join(_key(item) for item in path) + "]")
        for key, item in value.items():
            if not isinstance(item, dict):
                lines.append(f"{_key(key)} = {_scalar(item)}")
        for key, item in value.items():
            if isinstance(item, dict):
                if lines:
                    lines.append("")
                table([*path, key], item)

    table([], document)
    return "\n".join(lines) + "\n"


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, staged = tempfile.mkstemp(prefix=".codex-config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(render_toml(document))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        Path(staged).unlink(missing_ok=True)


def configure_home(configuration, home):
    _write(Path(home) / "config.toml", configuration["document"])


def preserve_provider_settings(configuration, home, arguments):
    """Apply explicit tuning after Omnigent generates the private provider table.

    The native launcher owns its credential-bearing provider definition and
    transport. Preserve that definition and restore only the Profile's explicit
    retry/stream ceilings, which the upstream launcher otherwise overwrites.
    """
    if "app-server" not in arguments:
        return
    path = Path(home) / "config.toml"
    document = tomllib.loads(path.read_text())
    selected = document.get("model_provider")
    for index, argument in enumerate(arguments):
        override = (
            arguments[index + 1]
            if argument in {"-c", "--config"} and index + 1 < len(arguments)
            else (
                argument.removeprefix("--config=")
                if argument.startswith("--config=")
                else ""
            )
        )
        if override.partition("=")[0] == "model_provider":
            selected = tomllib.loads(override)["model_provider"]
    providers = document.get("model_providers", {})
    if selected not in providers:
        raise ValueError("native Codex launch lost its selected provider")
    for key in ("request_max_retries", "stream_max_retries", "stream_idle_timeout_ms"):
        if key in configuration["provider"]:
            providers[selected][key] = configuration["provider"][key]
    _write(path, document)


def require_reusable_context(home, arguments):
    """Refuse a fresh native start when a continuation was explicitly admitted.

    Omnigent clones a local rollout or rebuilds one from its durable items
    before starting app-server. Its best-effort fallback must not silently
    erase MoonMind's requested same-epoch context.
    """
    if "app-server" not in arguments:
        return
    root = Path(home).resolve() / "sessions"
    for path in root.rglob("*.jsonl"):
        if (
            path.is_symlink()
            or not path.is_file()
            or root not in path.resolve().parents
        ):
            continue
        metadata = False
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except (ValueError, TypeError):
                    break
                if not isinstance(item, dict):
                    break
                payload = item.get("payload") or {}
                if not isinstance(payload, dict):
                    break
                if item.get("type") == "session_meta":
                    metadata = payload.get("cwd") == str(Path.cwd())
                if (
                    metadata
                    and item.get("type") == "compacted"
                    and (payload.get("replacement_history") or payload.get("message"))
                ):
                    return
                if (
                    metadata
                    and item.get("type") == "response_item"
                    and payload.get("type") == "message"
                    and payload.get("content")
                ):
                    return
    raise ValueError(
        "requested native context was not restored; refusing a fresh Codex conversation"
    )


if __name__ == "__main__":
    action, config_path, *arguments = sys.argv[1:]
    configuration = (
        json.loads(Path(config_path).read_text()) if config_path != "-" else None
    )
    if action == "prepare":
        if configuration is not None:
            configure_home(configuration, os.environ["CODEX_HOME"])
        binary, wrapper, context = arguments
        launch = [context, sys.executable, __file__, "launch", config_path, binary]
        Path(wrapper).write_text("#!/bin/sh\nexec " + shlex.join(launch) + ' "$@"\n')
        Path(wrapper).chmod(0o700)
    elif action == "launch":
        binary, *arguments = arguments
        if os.environ.get("MOONMIND_CODEX_REQUIRE_CONTEXT") == "1":
            require_reusable_context(os.environ["CODEX_HOME"], arguments)
        if configuration is not None:
            preserve_provider_settings(
                configuration, os.environ["CODEX_HOME"], arguments
            )
        os.execv(binary, [binary, *arguments])
    else:
        raise SystemExit("unsupported Codex configuration operation")
