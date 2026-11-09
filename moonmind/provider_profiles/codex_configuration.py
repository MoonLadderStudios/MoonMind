"""Codex Profile configuration shared by direct and rendered launches.

This module is stdlib-only so the same serializer and private-home adjustment
can be packaged into a host without importing MoonMind or its database.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path
from types import SimpleNamespace

_CONFIG_PATH = "{{runtime_support_dir}}/codex-home/config.toml"
_CODEX_HOME = "{{runtime_support_dir}}/codex-home"
_ENROLLED_CODEX_HOME = "/home/app/.codex"
MINIMAX_MODEL = "codex-MiniMax-M2.7"
OAUTH_CONFIGURATION_ENV = "MOONMIND_CODEX_PROFILE_CONFIGURATION"
_SESSION_SETTINGS = {
    "developer_instructions", "hide_agent_reasoning", "show_raw_agent_reasoning",
    "project_doc_max_bytes", "project_doc_fallback_filenames", "history",
    "model_reasoning_summary", "model_verbosity", "model_context_window",
    "model_auto_compact_token_limit", "personality",
}


def _validate_session_settings(document):
    """Carry session preferences without admitting another tool/auth owner."""
    if "developer_instructions" in document and not isinstance(document["developer_instructions"], str):
        raise ValueError("developer_instructions must be a string")
    for key in ("hide_agent_reasoning", "show_raw_agent_reasoning"):
        if key in document and type(document[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    if "project_doc_max_bytes" in document and (
        type(document["project_doc_max_bytes"]) is not int or document["project_doc_max_bytes"] < 0
    ):
        raise ValueError("project_doc_max_bytes must be a nonnegative integer")
    if "project_doc_fallback_filenames" in document:
        names = document["project_doc_fallback_filenames"]
        if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
            raise ValueError("project_doc_fallback_filenames must be a string array")
    if "history" in document:
        history = document["history"]
        if not isinstance(history, dict) or set(history) - {"persistence", "max_bytes"}:
            raise ValueError("history contains unsupported settings")
        if "persistence" in history and history["persistence"] not in {"save-all", "none"}:
            raise ValueError("history persistence is unsupported")
        if "max_bytes" in history and (type(history["max_bytes"]) is not int or history["max_bytes"] < 0):
            raise ValueError("history max_bytes must be a nonnegative integer")


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


def _validate_additional_file_path(relative):
    """Protect runtime-owned paths before and after context resolution."""
    if (
        relative.is_absolute() or ".." in relative.parts or not relative.parts
        or relative.parts[0] in {
            "auth.json", ".credentials.json", "config.toml", "hooks.json",
            "sessions", "archived_sessions", "plugins", "mcp-oauth-locks",
        }
        or relative.parts[0].endswith((".sqlite", ".sqlite-wal", ".sqlite-shm"))
        or any(part.startswith((".moonmind-", ".omnigent-")) for part in relative.parts)
    ):
        raise ValueError("additional Codex files cannot replace authentication, history or runtime-owned files")


def _translate_private_home_context(value, home_path):
    if isinstance(value, str):
        if home_path != "{{runtime_support_dir}}":
            parts = value.split(home_path)
            if (
                any("{{runtime_support_dir}}" in part for part in parts)
                or any(part and not part.startswith("/") for part in parts[1:])
            ):
                raise ValueError("additional Codex files reference an untranslated support path")
            value = "{{runtime_support_dir}}".join(parts)
        checked = value.replace("{{runtime_support_dir}}", "").replace("{{workspace_path}}", "")
        if _has_authority(checked):
            raise ValueError("additional Codex files must not contain credential templates")
        return value
    if isinstance(value, dict):
        if _has_authority({key: None for key in value}):
            raise ValueError("additional Codex files cannot introduce authentication")
        return {key: _translate_private_home_context(item, home_path) for key, item in value.items()}
    if isinstance(value, list):
        return [_translate_private_home_context(item, home_path) for item in value]
    return value


def _additional_file(template, home_path):
    """Translate a declared private-home file without granting auth/history writes."""
    prefix = home_path.rstrip("/") + "/"
    path = str(template["path"] or "")
    if not path.startswith(prefix):
        raise ValueError("additional Codex files must remain within the declared HOME")
    relative = Path(path[len(prefix):])
    _validate_additional_file_path(relative)
    # The private-home prefix was already translated above. Only workspace
    # context remains equivalent in the relative filename; SecretRef aliases
    # and other support-directory substitutions require their native owner.
    available_path = re.sub(r"{{\s*workspace_path\s*}}", "", str(relative))
    if _has_authority(available_path):
        raise ValueError("additional Codex file paths require available nonsecret context")
    if int(template["permissions"], 8) != 0o600:
        raise ValueError("additional Codex files require private permissions")
    if template["format"] not in {"text", "json", "toml"} or template["merge_strategy"] not in {"replace", "deep_merge", "append"}:
        raise ValueError("additional Codex file rendering is unsupported")

    return {
        **template,
        "path": "{{runtime_support_dir}}/" + relative.as_posix(),
        "content_template": _translate_private_home_context(template["content_template"], home_path),
    }


def _configuration_document(profile):
    """Read private-HOME templates once for both admitted auth models."""
    try:
        templates, repaired = repaired_managed_templates(profile)
        home = dict(getattr(profile, "home_path_overrides", None) or {})
        credential_source = getattr(profile, "credential_source", None)
        materialization_mode = getattr(profile, "runtime_materialization_mode", None)
        enrolled_home = (
            getattr(profile, "runtime_id", None) == "codex_cli"
            and getattr(credential_source, "value", credential_source) == "oauth_volume"
            and getattr(materialization_mode, "value", materialization_mode) == "oauth_home"
            and getattr(profile, "volume_mount_path", None) == _ENROLLED_CODEX_HOME
        )
        if not home and enrolled_home:
            home_path = _ENROLLED_CODEX_HOME
        elif set(home) == {"CODEX_HOME"}:
            home_path = str(home["CODEX_HOME"])
        else:
            return None, "Codex file templates require one declared CODEX_HOME"
        if (
            not (
                home_path.startswith("{{runtime_support_dir}}/")
                or enrolled_home and home_path == _ENROLLED_CODEX_HOME
            )
            or ".." in Path(home_path).parts
        ):
            return None, "Codex HOME must be within its run support directory"
        document = {}
        additional_files = []
        if not templates:
            return None, "Codex file templates are empty"
        for template in templates:
            if template["path"] != home_path.rstrip("/") + "/config.toml":
                additional_files.append(_additional_file(template, home_path))
                continue
            if (
                template["format"] != "toml"
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
        return {"document": document, "additionalFiles": additional_files, "homePath": home_path,
                "managedRecipeRepaired": repaired}, ""
    except (TypeError, ValueError, KeyError):
        return None, "Codex file templates contain invalid configuration"


def configuration_for_profile(profile):
    """Return one declared Codex config and its selected key role, or a reason."""
    try:
        parsed, reason = _configuration_document(profile)
        if parsed is None:
            return None, reason
        document = parsed["document"]
        additional_files = parsed["additionalFiles"]
        repaired = parsed["managedRecipeRepaired"]
        supported_settings = _SESSION_SETTINGS | {
            "model",
            "model_provider",
            "model_providers",
            "model_reasoning_effort",
        }
        if set(document) - supported_settings:
            return (
                None,
                "Codex configuration includes settings outside the shared model/session configuration",
            )
        _validate_session_settings(document)
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
            or key_name not in environment
        ):
            return None, "Codex config requires its exact declared key environment"
        launch_environment = {key: value for key, value in environment.items() if key != key_name}
        if any(not isinstance(value, str) for value in launch_environment.values()):
            return None, "Codex project environment requires nonsecret values"
        from moonmind.omnigent.host_services.runtime_scripts import validate_provider_literal_launch_environment

        launch_environment = validate_provider_literal_launch_environment(launch_environment)
        from moonmind.provider_profiles.template_renderer import validate_environment_template_context

        environment_templates = {key: value for key, value in launch_environment.items() if "{{" in value}
        environment_templates = validate_environment_template_context(environment_templates)
        for value in environment_templates.values():
            normalized = re.sub(r"{{\s*runtime_support_dir\s*}}", "{{runtime_support_dir}}", value)
            # Keep ambiguous support paths on the direct route rather than
            # treating an alternate spelling of the relocated HOME as generic.
            if "{{runtime_support_dir}}" in normalized and any(
                "//" in path or re.search(r"/\.{1,2}(?:/|$)", path)
                for path in (normalized, parsed["homePath"])
            ):
                return None, "Codex HOME-relative or ambiguous environment requires native delivery"
            # Native sessions relocate the declared HOME. Generic runtime paths
            # are equivalent, but references into that HOME need its own owner.
            if re.search(re.escape(parsed["homePath"].rstrip("/")) + r"(?=/|$)", normalized):
                return None, "Codex HOME-relative environment requires native delivery"
        launch_environment = {key: value for key, value in launch_environment.items() if key not in environment_templates}
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
            **({"environmentTemplates": environment_templates} if environment_templates else {}),
            **({"additionalFiles": additional_files} if additional_files else {}),
            **({"launchEnvironment": launch_environment} if launch_environment else {}),
        }, ""
    except (TypeError, ValueError, KeyError):
        return None, "Codex file templates contain invalid configuration"


def validate_oauth_configuration(configuration):
    """Validate preferences/files without replacing OAuth, tools or provider state."""
    if (
        not isinstance(configuration, dict)
        or set(configuration) - {"document", "additionalFiles", "privateHomeOnly", "environmentTemplates"}
        or configuration.get("privateHomeOnly") is not True
    ):
        raise ValueError("OAuth Codex configuration requires private-HOME delivery")
    document = configuration.get("document")
    supported = _SESSION_SETTINGS
    if not isinstance(document, dict) or set(document) - supported or _has_authority(document):
        raise ValueError("OAuth Codex configuration contains unsupported model, provider, tool or credential settings")
    _validate_session_settings(document)
    render_toml(document)
    files = configuration.get("additionalFiles") or []
    if not isinstance(files, list):
        raise ValueError("OAuth Codex file configuration must be an array")
    files = [_additional_file(_template(item), "{{runtime_support_dir}}") for item in files]
    environment = configuration.get("environmentTemplates") or {}
    if not isinstance(environment, dict):
        raise ValueError("private Codex environment templates must be an object")
    if __package__:
        from .template_renderer import validate_environment_template_context
    else:
        from template_renderer import validate_environment_template_context
    environment = validate_environment_template_context(environment)
    result = {
        "document": copy.deepcopy(document), "privateHomeOnly": True,
        **({"additionalFiles": files} if files else {}),
        **({"environmentTemplates": environment} if environment else {}),
    }
    if len(json.dumps(result).encode()) > 65536:
        raise ValueError("OAuth Codex private configuration exceeds its delivery bound")
    return result


def oauth_configuration_for_profile(profile):
    parsed, reason = _configuration_document(profile)
    if parsed is None:
        return None, reason
    try:
        # Only template-derived references to the relocated HOME bind late.
        # Absolute literals and generic support/workspace paths retain their owner.
        environment = {}
        home_path = parsed["homePath"]
        if home_path.startswith("{{runtime_support_dir}}/"):
            for key, value in (getattr(profile, "env_template", None) or {}).items():
                if not isinstance(value, str):
                    continue
                normalized = re.sub(r"{{\s*runtime_support_dir\s*}}", "{{runtime_support_dir}}", value)
                if re.search(re.escape(home_path) + r"(?=/|$)", normalized):
                    environment[key] = _translate_private_home_context(normalized, home_path)
        if environment:
            from moonmind.omnigent.host_services.runtime_scripts import validate_provider_literal_launch_environment

            environment = validate_provider_literal_launch_environment(environment)
        return validate_oauth_configuration({
            "document": parsed["document"], "privateHomeOnly": True,
            "additionalFiles": parsed["additionalFiles"],
            **({"environmentTemplates": environment} if environment else {}),
        }), ""
    except (TypeError, ValueError, KeyError) as error:
        return None, str(error)


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


def configure_private_oauth_home(configuration, home):
    """Overlay preferences only after the native owner created its private HOME."""
    home = Path(home).resolve()
    enrolled = Path(_ENROLLED_CODEX_HOME).resolve()
    if home == enrolled or enrolled in home.parents or not home.is_dir():
        raise ValueError("OAuth preferences cannot modify the enrolled credential home")
    path = home / "config.toml"
    if path.is_symlink():
        raise ValueError("private Codex configuration cannot be a symbolic link")
    document = tomllib.loads(path.read_text()) if path.exists() else {}
    preferences = copy.deepcopy(configuration["document"])
    addition = preferences.get("developer_instructions")
    active = document.get("developer_instructions")
    if addition is not None and isinstance(active, str) and active:
        # Native launch already added the agent's instructions. Preserve that
        # authority alongside the Profile preference, including on relaunch.
        preferences["developer_instructions"] = (
            f"{active}\n\n{addition}"
            if addition and active != addition and not active.endswith(f"\n\n{addition}")
            else active
        )
    _write(path, _merge(document, preferences))


def _privatize_native_file_path(home, rendered_path):
    """Detach the selected native HOME links without changing their source."""
    relative = Path(rendered_path).relative_to(home)
    _validate_additional_file_path(relative)
    source_home = Path(_ENROLLED_CODEX_HOME).resolve()
    for length in range(1, len(relative.parts) + 1):
        path = home.joinpath(*relative.parts[:length])
        if not path.is_symlink():
            if path.is_file() and path.stat().st_nlink != 1:
                raise ValueError("Profile files cannot modify an unowned hard link")
            continue
        source = path.resolve(strict=True)
        if source != source_home.joinpath(*relative.parts[:length]):
            raise ValueError("Profile files must stay within runtime_support_dir and cannot follow an unowned symbolic link")
        if source.is_dir():
            staged = Path(tempfile.mkdtemp(prefix=".moonmind-profile-dir-", dir=path.parent))
            try:
                # Keep untouched global entries visible without copying a
                # potentially large cache; only the selected path is detached.
                for child in source.iterdir():
                    (staged / child.name).symlink_to(child)
                path.unlink()
                os.replace(staged, path)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        elif source.is_file():
            descriptor, staged = tempfile.mkstemp(prefix=".moonmind-profile-file-", dir=path.parent)
            os.close(descriptor)
            try:
                shutil.copyfile(source, staged)
                os.replace(staged, path)
            finally:
                Path(staged).unlink(missing_ok=True)
        else:
            raise ValueError("Profile files require a regular native source")


def configure_profile_files(configuration, home, workspace):
    """Render per-run files only in the native process's private Codex home.

    A completed projection is reused on process restart. An interrupted write
    stops visibly rather than repeating append operations whose effect is
    uncertain; ordinary step recovery provisions a fresh private home.
    """
    templates = [
        _additional_file(_template(template), "{{runtime_support_dir}}")
        for template in configuration.get("additionalFiles") or []
    ]
    if not templates:
        return
    if __package__:
        from .template_renderer import ProfileTemplateRenderer
    else:
        from template_renderer import ProfileTemplateRenderer

    home = Path(home).resolve()
    enrolled = Path(_ENROLLED_CODEX_HOME).resolve()
    if home == enrolled or enrolled in home.parents or not home.is_dir():
        raise ValueError("Profile files cannot modify the enrolled credential home")
    workspace = Path(workspace).resolve()
    digest = hashlib.sha256(json.dumps(
        {"templates": templates, "workspace": str(workspace)},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    marker = home / ".moonmind-profile-file-digest"
    if marker.is_symlink():
        raise ValueError("Profile file projection marker is not privately owned")
    if marker.exists():
        if marker.read_text() == digest:
            return
        raise ValueError("Profile file projection is interrupted or conflicts with this launch")
    with marker.open("x") as stream:
        marker.chmod(0o600)
        stream.write("preparing")
    renderer = ProfileTemplateRenderer(toml_serializer=render_toml)
    context = {"runtime_support_dir": str(home), "workspace_path": str(workspace)}
    for template in templates:
        _privatize_native_file_path(home, renderer._render_string(template["path"], context))
        renderer._materialize_file_template(
            SimpleNamespace(**template),
            context,
        )
    marker.write_text(digest)


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
    if configuration is not None and configuration.get("privateHomeOnly"):
        configuration = validate_oauth_configuration(configuration)
    if action == "prepare":
        if configuration is not None and not configuration.get("privateHomeOnly"):
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
            if "app-server" in arguments:
                if configuration.get("privateHomeOnly"):
                    configure_private_oauth_home(configuration, os.environ["CODEX_HOME"])
                configure_profile_files(configuration, os.environ["CODEX_HOME"], Path.cwd())
                if configuration.get("environmentTemplates"):
                    from template_renderer import ProfileTemplateRenderer

                    os.environ.update(ProfileTemplateRenderer(toml_serializer=render_toml).render_environment(
                        configuration["environmentTemplates"],
                        {"runtime_support_dir": str(Path(os.environ["CODEX_HOME"]).resolve()),
                         "workspace_path": str(Path.cwd().resolve())},
                    ))
            if not configuration.get("privateHomeOnly"):
                preserve_provider_settings(
                    configuration, os.environ["CODEX_HOME"], arguments
                )
        os.execv(binary, [binary, *arguments])
    else:
        raise SystemExit("unsupported Codex configuration operation")
