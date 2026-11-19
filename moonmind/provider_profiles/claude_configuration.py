"""Thin Claude preference adapter over the shared Profile template renderer.

Only nonsecret preference files are authored here. Authentication,
model selection and tools continue to belong to the admitted shared launch.
This module is portable so hosts run the same validation as admission.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

try:
    from .template_renderer import ProfileTemplateRenderer, template_context_names
except ImportError:  # Packaged into the run-owned host context.
    from template_renderer import ProfileTemplateRenderer, template_context_names

_HOME = "{{runtime_support_dir}}/claude-home"
_SETTINGS = {
    "outputStyle",
    "language",
    "alwaysThinkingEnabled",
    "spinnerTipsEnabled",
    "permissions",
}


_DENY_TOOLS = {
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
}


def merge_disallowed_tools(args: list[str], tools: list[str]) -> None:
    """Add ``tools`` to the comma-separated ``--disallowedTools`` value.

    Merges into an authored flag (either spelling) so it is not overridden,
    matching how the Omnigent Claude harness merges its own denies.
    """

    if not tools:
        return
    # Normalize aliases/joined values before upstream augmentation, whose merge
    # recognizes only the separated canonical spelling. Otherwise its later flag
    # can replace the authored restrictions in the vendor CLI.
    for index, arg in enumerate(args):
        if arg.startswith(("--disallowedTools=", "--disallowed-tools=")):
            value = arg.split("=", 1)[1]
            existing = [tool for tool in value.split(",") if tool]
            args[index : index + 1] = [
                "--disallowedTools",
                ",".join(dict.fromkeys([*existing, *tools])),
            ]
            return
        if arg in {"--disallowedTools", "--disallowed-tools"} and index + 1 < len(args):
            existing = [tool for tool in args[index + 1].split(",") if tool]
            args[index] = "--disallowedTools"
            args[index + 1] = ",".join(dict.fromkeys([*existing, *tools]))
            return
    args.extend(["--disallowedTools", ",".join(tools)])


def validate_denied_tools(rules):
    """Literal built-in restrictions representable by the shared CLI transport."""
    if not isinstance(rules, list) or len(rules) > 64:
        raise ValueError("Claude deny rules require a bounded list")
    for rule in rules:
        if not isinstance(rule, str) or len(rule) > 256:
            raise ValueError("Claude deny rules require bounded literal strings")
        match = re.fullmatch(r"([A-Za-z]+)(?:\(([A-Za-z0-9_./*? :~-]+)\))?", rule)
        if not match or match[1] not in _DENY_TOOLS:
            raise ValueError("Claude deny rules require literal built-in tool patterns")
        if match[2] is not None and match[1] not in {
            "Read",
            "Edit",
            "Bash",
        }:
            raise ValueError("Claude deny pattern requires a supported built-in tool")
    return list(dict.fromkeys(rules))


def claude_denied_tools_from_handles(credential_handles):
    """Return the exact host's immutable selected Claude restrictions."""
    selections = []
    for handle in credential_handles:
        rules = handle.get("claudeDeniedTools")
        if rules is None:
            continue
        if (
            handle.get("materializerRef")
            not in {"claude-oauth-home@1", "omnigent-provider-config@1"}
            or validate_denied_tools(rules) != rules
        ):
            raise ValueError("Claude deny rules require their selected configuration")
        if rules not in selections:
            selections.append(rules)
    if len(selections) > 1:
        raise ValueError("conflicting Claude deny configurations")
    return selections[0] if selections else []


def project_denied_tools(*, harness_id, arguments, rules):
    """Apply frozen Claude policy through its thin provider argument adapter."""
    if harness_id != "claude-native":
        raise ValueError("Claude deny projection requires the Claude harness")
    if not isinstance(arguments, list) or any(not isinstance(arg, str) for arg in arguments):
        raise ValueError("Claude launch arguments must be a string array")
    flags = [
        index for index, arg in enumerate(arguments)
        if arg.split("=", 1)[0] in {"--disallowedTools", "--disallowed-tools"}
    ]
    if len(flags) > 1 or any(
        "=" not in arguments[index]
        and (index + 1 == len(arguments) or arguments[index + 1].startswith("--"))
        for index in flags
    ):
        raise ValueError("Claude deny projection requires unambiguous tool arguments")
    projected = list(arguments)
    merge_disallowed_tools(projected, validate_denied_tools(rules))
    return projected


def denied_tools(configuration):
    """Extract immutable restrictions, never consult a mutable Profile/home."""
    if configuration is None:
        return []
    home = (configuration.get("homePathOverrides") or {}).get("CLAUDE_CONFIG_DIR")
    checked = (
        validate_configuration(configuration)
        if isinstance(home, str) and home.startswith("{{runtime_support_dir}}/")
        else validate_oauth_configuration(configuration)
    )
    for item in checked["fileTemplates"]:
        if item["path"].endswith("/settings.json"):
            permissions = item["content_template"].get("permissions")
            if permissions is not None:
                rules = validate_denied_tools(permissions["deny"])
                # Only the worker extracts launch arguments; the portable host
                # renderer consumes a configuration already scanned here.
                from moonmind.omnigent.conformance import assert_secret_free

                assert_secret_free(rules)
                return rules
    return []


def validate_configuration(configuration):
    if set(configuration) != {"homePathOverrides", "fileTemplates"}:
        raise ValueError("unsupported Claude configuration fields")
    overrides = configuration["homePathOverrides"]
    home = overrides.get("CLAUDE_CONFIG_DIR") if isinstance(overrides, dict) else None
    # A named *-home child of the attempt's support directory has the same isolation
    # as the stock home. Never remap a credential home, another harness's home,
    # nested paths or the runtime support owner itself.
    prefix = "{{runtime_support_dir}}/"
    name = home.removeprefix(prefix) if isinstance(home, str) else ""
    if (
        not isinstance(overrides, dict) or set(overrides) - {"CLAUDE_CONFIG_DIR", "HOME"}
        or not isinstance(home, str) or not home.startswith(prefix)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*-home", name)
        or name == "codex-home"
    ):
        raise ValueError("home path overrides require an attempt-private Claude configuration home")
    process_home = overrides.get("HOME")
    process_files = []
    source_files = [
        raw.model_dump(by_alias=False, mode="json") if hasattr(raw, "model_dump") else raw
        for raw in configuration["fileTemplates"]
    ]
    if any(not isinstance(raw, dict) for raw in source_files):
        raise ValueError("Claude file templates must be objects")
    if "HOME" in overrides:
        if not isinstance(process_home, str):
            raise ValueError("process HOME requires an isolated private child")
        if process_home == home:
            raise ValueError("process HOME and Claude configuration home must be distinct")
        private = validate_oauth_configuration({
            "homePathOverrides": {"HOME": process_home},
            "fileTemplates": [raw for raw in source_files if str(raw.get("path", "")).startswith(str(process_home) + "/")],
        })
        process_files = private["fileTemplates"]
        source_files = [raw for raw in source_files if not str(raw.get("path", "")).startswith(str(process_home) + "/")]
    files = []
    for raw in source_files:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(by_alias=False, mode="json")
        if not isinstance(raw, dict):
            raise ValueError("Claude file templates must be objects")
        item = {
            "path": raw.get("path"), "format": raw.get("format", "text"),
            "content_template": raw.get("content_template", raw.get("contentTemplate", "")),
            "merge_strategy": raw.get("merge_strategy", raw.get("mergeStrategy", "replace")),
            "permissions": raw.get("permissions", "0600"),
        }
        path = str(item["path"] or "")
        relative = path.removeprefix(home + "/")
        if (not path.startswith(home + "/") or ".." in Path(relative).parts
            or any(ord(character) < 32 or ord(character) == 127 for character in path)):
            raise ValueError("Claude file templates must remain within the private home")
        # These are declarative preferences/instructions, not authentication,
        # hooks, plugins, persisted session state or another launch policy.
        if relative == "settings.json":
            content = item["content_template"]
            if item["format"] != "json" or not isinstance(content, dict) or set(content) - _SETTINGS:
                raise ValueError("Claude settings contain native-only options")
            for key, value in content.items():
                if key == "permissions":
                    if not isinstance(value, dict) or set(value) != {"deny"}:
                        raise ValueError("Claude permissions support deny-only rules")
                    validate_denied_tools(value["deny"])
                    continue
                expected = bool if key.endswith("Enabled") else str
                if type(value) is not expected:
                    raise ValueError("invalid Claude session preference")
        elif relative == "CLAUDE.md" or (
            relative.startswith("rules/") and relative.endswith(".md")
            and "{{" not in relative
        ) or re.fullmatch(r"output-styles/[A-Za-z0-9][A-Za-z0-9_.-]*\.md", relative):
            # Output styles are declarative Markdown selected by outputStyle.
            # Only literal files in the vendor directory join the shared home;
            # executable hooks, plugins and credential files remain excluded.
            if item["format"] != "text" or not isinstance(item["content_template"], str):
                raise ValueError("Claude instruction files require text")
        else:
            raise ValueError("Claude file templates contain native-only paths")
        if template_context_names(item["content_template"]) - {"workspace_path", "runtime_support_dir"}:
            raise ValueError("Claude file templates require unavailable or secret context")
        if item["merge_strategy"] not in {"replace", "append", "deep_merge"}:
            raise ValueError("unsupported Claude file merge strategy")
        if item["merge_strategy"] == "deep_merge" and item["format"] != "json":
            raise ValueError("Claude deep_merge requires JSON")
        if ProfileTemplateRenderer._parse_permissions(item["permissions"]) != 0o600:
            raise ValueError("Claude file templates require private permissions")
        files.append(item)
    settings = [item for item in files if item["path"] == home + "/settings.json"]
    if any("permissions" in item["content_template"] for item in settings):
        if settings[0]["merge_strategy"] != "replace" or any(
            item["merge_strategy"] not in {"replace", "deep_merge"}
            for item in settings
        ):
            raise ValueError("Claude deny settings require an explicit replacement seed")
        # Resolve only declared templates, using the native renderer's ordering
        # and array-replacement semantics. Never merge ambient enrolled settings
        # or union restrictions superseded by a later template.
        renderer = ProfileTemplateRenderer(toml_serializer=lambda _: "")
        effective = {}
        for item in settings:
            effective = (
                dict(item["content_template"])
                if item["merge_strategy"] == "replace"
                else renderer._deep_merge_dicts(effective, item["content_template"])
            )
        folded = {
            **settings[-1],
            "merge_strategy": "replace",
            "content_template": copy.deepcopy(effective),
        }
        files = [
            folded if item is settings[-1] else item
            for item in files
            if item["path"] != home + "/settings.json" or item is settings[-1]
        ]
    return {"homePathOverrides": dict(overrides), "fileTemplates": files + process_files}


def configuration_for_profile(profile):
    try:
        configuration = validate_configuration(
            {
                "homePathOverrides": dict(
                    getattr(profile, "home_path_overrides", None) or {}
                ),
                "fileTemplates": list(getattr(profile, "file_templates", None) or []),
            }
        )
        denied_tools(configuration)
        return configuration, ""
    except (ValueError, TypeError) as error:
        return None, str(error)


def materialize_configuration(
    configuration, *, runtime_support_dir, workspace_path, template_support_dir=None
):
    """Initialize an attempt's home once; retries never append twice."""
    configuration = validate_configuration(configuration)
    home = configuration["homePathOverrides"]["CLAUDE_CONFIG_DIR"]
    private = {"homePathOverrides": {"CLAUDE_CONFIG_DIR": home}, "fileTemplates": [
        item for item in configuration["fileTemplates"] if item["path"].startswith(home + "/")
    ]}
    return _materialize_private_files(private, runtime_support_dir=runtime_support_dir, workspace_path=workspace_path, template_support_dir=template_support_dir)


def _materialize_private_files(configuration, *, runtime_support_dir, workspace_path, template_support_dir=None, process_home=False):
    support = Path(runtime_support_dir).absolute() if process_home else Path(runtime_support_dir).resolve()
    if process_home and any(path.is_symlink() for path in (support, *support.parents)):
        raise ValueError("private configuration parent contains a symlink")
    name = configuration["homePathOverrides"]["CLAUDE_CONFIG_DIR"].split("/", 1)[1]
    home = support / name
    marker = home / ".moonmind-profile-digest"
    digest = hashlib.sha256(json.dumps([configuration, workspace_path], sort_keys=True).encode()).hexdigest()
    if home.exists() or home.is_symlink():
        if (home.is_symlink()
            or process_home and (not home.is_dir() or home.stat().st_uid != os.getuid()
                or home.stat().st_mode & 0o777 != 0o700 or marker.is_symlink())
            or not marker.is_file() or marker.read_text() != digest):
            raise ValueError("Claude private configuration home is already owned")
        if process_home and any(path.is_symlink() for path in home.rglob("*")):
            raise ValueError("private configuration contains a symlink")
        return str(home)
    support.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=support) as temporary:
        stage = Path(temporary)
        renderer = ProfileTemplateRenderer(toml_serializer=lambda _: "")
        context = {
            "runtime_support_dir": str(template_support_dir or support),
            "workspace_path": workspace_path,
        }
        for item in configuration["fileTemplates"]:
            # Render paths into staging but retain the real attempt paths in content.
            rendered = renderer._render_value(item["content_template"], context)
            staged = {**item, "content_template": rendered,
                      "path": item["path"].replace("{{runtime_support_dir}}", str(stage))}
            renderer._materialize_file_template(SimpleNamespace(**staged), {"runtime_support_dir": str(stage), "workspace_path": workspace_path})
        staged_home = stage / name
        staged_home.mkdir(mode=0o700, exist_ok=True)
        staged_home.chmod(0o700)
        (staged_home / marker.name).write_text(digest)
        (staged_home / marker.name).chmod(0o600)
        os.rename(staged_home, home)
    return str(home)


_ENROLLED_HOME = "/home/app/.claude"
OAUTH_PROJECTION_PATH = "/run/moonmind-claude-preferences"


def validate_oauth_configuration(configuration):
    """Accept canonical enrollment selectors without moving credential state."""
    if set(configuration) != {"homePathOverrides", "fileTemplates"}:
        raise ValueError("unsupported Claude OAuth configuration fields")
    overrides = configuration["homePathOverrides"]
    if not isinstance(overrides, dict) or any(
        key not in {"CLAUDE_HOME", "CLAUDE_CONFIG_DIR", "HOME"}
        or key != "HOME" and value != _ENROLLED_HOME
        for key, value in overrides.items()
    ):
        raise ValueError("Claude OAuth preferences require the enrolled home")
    process_home = overrides.get("HOME")
    if process_home is not None and (
        not isinstance(process_home, str)
        or not re.fullmatch(r"{{runtime_support_dir}}/[A-Za-z0-9][A-Za-z0-9_.-]*-home", process_home)
        or process_home.rsplit("/", 1)[1] in {"claude-home", "codex-home"}
    ):
        raise ValueError("process HOME requires an isolated private child")
    translated = []
    process_files = []
    for raw in configuration["fileTemplates"]:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(by_alias=False, mode="json")
        if isinstance(raw, dict) and process_home and str(raw.get("path", "")).startswith(process_home + "/"):
            relative = raw["path"][len(process_home) + 1:]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.md", relative):
                raise ValueError("process HOME files must be literal top-level Markdown")
            # Reuse the existing text/template/permissions validator without
            # granting new settings or executable configuration authority.
            checked = validate_configuration({
                "homePathOverrides": {"CLAUDE_CONFIG_DIR": _HOME},
                "fileTemplates": [{**raw, "path": _HOME + "/CLAUDE.md"}],
            })["fileTemplates"][0]
            process_files.append({**checked, "path": raw["path"]})
            continue
        if not isinstance(raw, dict) or not str(raw.get("path", "")).startswith(
            _ENROLLED_HOME + "/"
        ):
            raise ValueError("Claude OAuth files require canonical enrolled-home paths")
        if any(character in raw["path"] for character in (",", '"')):
            raise ValueError(
                "Claude OAuth file paths must be representable as Docker mounts"
            )
        translated.append({**raw, "path": _HOME + raw["path"][len(_ENROLLED_HOME) :]})
    validated = validate_configuration(
        {
            "homePathOverrides": {"CLAUDE_CONFIG_DIR": _HOME},
            "fileTemplates": translated,
        }
    )
    return {
        "homePathOverrides": dict(overrides),
        "fileTemplates": [
            {**item, "path": _ENROLLED_HOME + item["path"][len(_HOME) :]}
            for item in validated["fileTemplates"]
        ] + process_files,
    }


def oauth_configuration_for_profile(profile):
    try:
        configuration = validate_oauth_configuration(
            {
                "homePathOverrides": dict(
                    getattr(profile, "home_path_overrides", None) or {}
                ),
                "fileTemplates": list(getattr(profile, "file_templates", None) or []),
            }
        )
        denied_tools(configuration)
        return configuration, ""
    except (ValueError, TypeError) as error:
        return None, str(error)


def render_oauth_files(configuration, *, runtime_support_dir, workspace_path):
    """Use the same renderer, but return bytes for read-only file mounts.

    The credential directory is never copied or redirected. Docker overlays only
    these nonsecret files while atomic vendor refreshes retain the enrolled home.
    """
    configuration = validate_oauth_configuration(configuration)
    private = {
        "homePathOverrides": {"CLAUDE_CONFIG_DIR": _HOME},
        "fileTemplates": [
            {**item, "path": _HOME + item["path"][len(_ENROLLED_HOME) :]}
            for item in configuration["fileTemplates"]
            if item["path"].startswith(_ENROLLED_HOME + "/")
        ],
    }
    with tempfile.TemporaryDirectory() as directory:
        materialize_configuration(
            private,
            runtime_support_dir=directory,
            template_support_dir=runtime_support_dir,
            workspace_path=workspace_path,
        )
        root = Path(directory) / "claude-home"
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != ".moonmind-profile-digest"
        }


def process_home_configuration(configuration):
    """Return the validated process-home projection, separate from credential files."""
    if "HOME" not in configuration.get("homePathOverrides", {}):
        return None
    selector = configuration["homePathOverrides"].get("CLAUDE_CONFIG_DIR")
    configuration = (
        validate_configuration(configuration)
        if selector is not None and selector != _ENROLLED_HOME
        else validate_oauth_configuration(configuration)
    )
    home = configuration["homePathOverrides"]["HOME"]
    return {"homePathOverrides": {"HOME": home}, "fileTemplates": [
        item for item in configuration["fileTemplates"] if item["path"].startswith(home + "/")
    ]}


def materialize_process_home(configuration, *, runtime_support_dir, workspace_path):
    private = process_home_configuration(configuration)
    if private is None:
        return None
    home = private["homePathOverrides"]["HOME"]
    return _materialize_private_files(
        {"homePathOverrides": {"CLAUDE_CONFIG_DIR": home}, "fileTemplates": private["fileTemplates"]},
        runtime_support_dir=runtime_support_dir, workspace_path=workspace_path, process_home=True,
    )


def claude_process_home_from_handles(credential_handles):
    """Resolve the one frozen process-home projection for this exact host."""
    configurations = []
    for handle in credential_handles:
        configuration = handle.get("claudeProcessHomeConfiguration")
        if configuration is not None:
            validated = process_home_configuration(configuration)
            if (
                handle.get("materializerRef")
                not in {"claude-oauth-home@1", "omnigent-provider-config@1"}
                or validated != configuration
            ):
                raise ValueError("process HOME requires its selected Claude configuration")
            if configuration not in configurations:
                configurations.append(configuration)
    if len(configurations) > 1:
        raise ValueError("conflicting process HOME configurations")
    if not configurations:
        return None, None
    configuration = configurations[0]
    home = configuration["homePathOverrides"]["HOME"].replace(
        "{{runtime_support_dir}}", "/home/app/.omnigent/moonmind/runtime-context"
    )
    return home, configuration
