"""Generic host entrypoint construction."""

from __future__ import annotations

from typing import Any

_RUNTIME_BIN_DIR = "/home/app/.omnigent/moonmind/bin"
_RUNTIME_CONTEXT_DIR = "/home/app/.omnigent/moonmind/runtime-context"

_OPENCODE_RUNTIME_ENV = {
    # MoonMind qualifies the exact pinned image + credential + model before
    # launch. Re-fetching mutable catalog data inside every session duplicates
    # that authority and can block ``opencode serve`` before its loopback API is
    # ready. The compiled catalog remains available to the pinned binary.
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    # Omnigent supplies its policy bridge as an explicit configured plugin.
    # OpenCode's unrelated built-in auth plugins are not part of this host
    # class and add another unbounded initialization surface.
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    # The host image is digest-pinned; an in-session updater must not mutate or
    # delay that runtime after its deployment qualification has passed.
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
}

# The on-demand host receives these values from the sandbox egress boundary.
# Omnigent deliberately filters the host environment before spawning a runner,
# so their *names* must survive the host -> runner hop. The OpenCode server then
# inherits the values through its own restricted environment builder.
_OPENCODE_PROXY_ENV_NAMES = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)
_GITHUB_RUNTIME_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
    "GIT_CONFIG_VALUE_0": f"!{_RUNTIME_BIN_DIR}/gh auth git-credential",
    "GH_CONFIG_DIR": "/home/app/.config/gh",
    "GH_PROMPT_DISABLED": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
}
_MOONMIND_RUNTIME_ENV_FILES = {
    "MOONMIND_URL": "moonmind-url",
    "MOONMIND_AGENT_RUN_ID": "agent-run-id",
    "MOONMIND_TASK_WORKFLOW_ID": "task-workflow-id",
    "MOONMIND_STEP_ID": "step-id",
    "MOONMIND_RUNTIME_ID": "runtime-id",
    "MOONMIND_REPOSITORY_CONNECTION_REF": "repository-connection-ref",
    "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE": "execution-fanout-file",
}


class OmnigentRuntimeScriptService:
    def build_entrypoint(
        self,
        *,
        credential_handles: list[dict[str, Any]],
        skill_attachment: dict[str, Any],
        step_execution_id: str,
        tool_attachments: tuple[dict[str, Any], ...] = (),
        github_credential_attachment: dict[str, Any] | None = None,
        control_attachment: dict[str, Any] | None = None,
        control_credential_available: bool = True,
        enable_opencode_runtime: bool = False,
        runtime_environment: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        generation_checks: list[str] = []
        for handle in credential_handles:
            for attachment in handle.get("attachments", []):
                target = str(attachment.get("targetPath") or "")
                generation = int(handle["credentialGeneration"])
                if target:
                    generation_checks.append(
                        f"{target}/.moonmind-generation:{generation}"
                    )
        environment = {
            "MOONMIND_CREDENTIAL_GENERATION_CHECKS": ",".join(generation_checks),
            "MOONMIND_ACTIVE_SKILLS_DIR": str(skill_attachment["targetPath"]),
            "MOONMIND_STEP_EXECUTION_ID": step_execution_id,
        }
        supplied_runtime_environment = dict(runtime_environment or {})
        if set(supplied_runtime_environment) - set(_MOONMIND_RUNTIME_ENV_FILES):
            raise ValueError("unsupported MoonMind runtime environment")
        for key, value in supplied_runtime_environment.items():
            normalized = str(value or "").strip()
            if not normalized or any(
                character in normalized for character in ("\0", "\r", "\n")
            ):
                raise ValueError(f"invalid MoonMind runtime environment {key}")
            environment[key] = normalized
        staging_dir = "/run/mm-credentials/opencode"
        opencode_data = "/home/app/.local/share/opencode"
        has_opencode_materializer = any(
            str(attachment.get("targetPath") or "").rstrip("/") == staging_dir
            for handle in credential_handles
            for attachment in handle.get("attachments", [])
        )
        opencode_runtime = enable_opencode_runtime or has_opencode_materializer
        tool_bins = [
            str(item["targetPath"]).rstrip("/") + "/bin"
            for item in tool_attachments
            if item.get("targetPath")
        ]
        if opencode_runtime or github_credential_attachment is not None:
            tool_bins.insert(0, _RUNTIME_BIN_DIR)
        # Always include the Omnigent venv so `docker exec` probes (attestation,
        # version checks) resolve the omnigent binary without a full PATH.
        environment["PATH"] = ":".join(
            [*tool_bins, "/opt/venv/bin", "/usr/local/bin", "/usr/bin", "/bin"]
        )
        allowed_runtime_environment = {"OMNIGENT_CONFIG_HOME"}
        for handle in credential_handles:
            for key, value in dict(handle.get("runtimeEnvironment") or {}).items():
                if key not in allowed_runtime_environment or not str(value).startswith(
                    "/"
                ):
                    raise ValueError(
                        f"unsupported credential runtime environment {key}"
                    )
                existing = environment.get(key)
                if existing is not None and existing != value:
                    raise ValueError(
                        f"conflicting credential runtime environment {key}"
                    )
                environment[key] = str(value)
        control_path = (
            f"{control_attachment['targetPath']}/api-token"
            if control_attachment is not None and control_credential_available
            else ""
        )
        environment["MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE"] = control_path
        # Stage harness credentials from read-only volumes into the writable
        # tmpfs home. OpenCode needs to create repos/, cache/ etc. inside its
        # data directory, which a read-only credential mount would block.
        runtime_context_dir = _RUNTIME_CONTEXT_DIR
        opencode_context_wrapper = f"{_RUNTIME_BIN_DIR}/moonmind-opencode-context"
        opencode_wrapper = f"{_RUNTIME_BIN_DIR}/opencode"
        if opencode_runtime:
            environment["MOONMIND_OPENCODE_RUNTIME"] = "1"
            environment.update(_OPENCODE_RUNTIME_ENV)
        if github_credential_attachment is not None:
            if (
                str(github_credential_attachment.get("targetPath") or "")
                != "/run/mm-credentials/github"
            ):
                raise ValueError("GitHub credential attachment target is unsupported")
            environment.update(_GITHUB_RUNTIME_ENV)
        passthrough_names = {
            "MOONMIND_ACTIVE_SKILLS_DIR",
            "MOONMIND_STEP_EXECUTION_ID",
            *(_OPENCODE_RUNTIME_ENV if opencode_runtime else {}),
            *(_OPENCODE_PROXY_ENV_NAMES if opencode_runtime else {}),
            *(_GITHUB_RUNTIME_ENV if github_credential_attachment is not None else {}),
            *supplied_runtime_environment,
        }
        if passthrough_names:
            environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"] = ",".join(
                sorted(passthrough_names)
            )
        runtime_context_writes = "".join(
            f'printf \'%s\\n\' "${key}" > {runtime_context_dir}/{filename}; '
            for key, filename in _MOONMIND_RUNTIME_ENV_FILES.items()
            if key in supplied_runtime_environment
        )
        runtime_context_exports = "".join(
            f"'export {key}=$(cat {runtime_context_dir}/{filename})' "
            for key, filename in _MOONMIND_RUNTIME_ENV_FILES.items()
            if key in supplied_runtime_environment
        )
        script = (
            "set -eu; "
            "unset OPENAI_API_KEY ANTHROPIC_API_KEY OPENCODE_AUTH_CONTENT "
            "OPENCODE_CONFIG OPENCODE_CONFIG_CONTENT; "
            "oldifs=$IFS; IFS=,; "
            "for check in ${MOONMIND_CREDENTIAL_GENERATION_CHECKS:-}; do "
            "path=${check%:*}; generation=${check##*:}; "
            'test -r "$path"; test "$(cat "$path")" = "$generation"; done; '
            'IFS=$oldifs; test -d "$MOONMIND_ACTIVE_SKILLS_DIR"; '
            'if [ "${MOONMIND_OPENCODE_RUNTIME:-0}" = 1 ]; then '
            "mkdir -p "
            + opencode_data
            + " "
            + runtime_context_dir
            + " "
            + _RUNTIME_BIN_DIR
            + "; "
            "if [ -d "
            '"' + staging_dir + '"'
            " ]; then cp "
            '"' + staging_dir + '/auth.json" ' + opencode_data + "/auth.json; "
            "chown 1000:1000 " + opencode_data + "/auth.json; "
            "chmod 0600 " + opencode_data + "/auth.json; fi; "
            "printf '%s\\n' \"$MOONMIND_ACTIVE_SKILLS_DIR\" > "
            + runtime_context_dir
            + "/active-skills-dir; "
            "printf '%s\\n' \"$MOONMIND_STEP_EXECUTION_ID\" > "
            + runtime_context_dir
            + "/step-execution-id; "
            + runtime_context_writes
            + "chmod 0600 " + runtime_context_dir + "/*; "
            "printf '%s\\n' '#!/bin/sh' "
            "'export MOONMIND_ACTIVE_SKILLS_DIR=$(cat "
            + runtime_context_dir
            + "/active-skills-dir)' "
            "'export MOONMIND_STEP_EXECUTION_ID=$(cat "
            + runtime_context_dir
            + "/step-execution-id)' "
            + runtime_context_exports
            + "'if [ -r /home/app/.config/gh/hosts.yml ]; then' "
            "'  export GH_CONFIG_DIR=/home/app/.config/gh' "
            "'  export GH_PROMPT_DISABLED=1' "
            "'  export GH_NO_UPDATE_NOTIFIER=1' "
            "'  export GH_NO_EXTENSION_UPDATE_NOTIFIER=1' "
            "'  export GIT_CONFIG_COUNT=1' "
            "'  export GIT_CONFIG_KEY_0=credential.https://github.com.helper' "
            "'  export GIT_CONFIG_VALUE_0=\"!"
            + _RUNTIME_BIN_DIR
            + "/gh auth git-credential\"' "
            "'fi' 'exec \"$@\"' > " + opencode_context_wrapper + "; "
            "printf '%s\\n' '#!/bin/sh' "
            "'exec "
            + opencode_context_wrapper
            + ' /usr/local/bin/opencode "$@"\' > '
            + opencode_wrapper
            + "; "
            "chmod 0700 " + opencode_context_wrapper + " " + opencode_wrapper + "; fi; "
            "if [ -d /run/mm-credentials/github ]; then "
            "mkdir -p /home/app/.config/gh; "
            "cp /run/mm-credentials/github/hosts.yml "
            "/home/app/.config/gh/hosts.yml; "
            "chmod 0700 /home/app/.config/gh; "
            "chmod 0600 /home/app/.config/gh/hosts.yml; "
            "mkdir -p " + _RUNTIME_BIN_DIR + "; "
            "printf '%s\\n' '#!/bin/sh' "
            "'export GH_CONFIG_DIR=/home/app/.config/gh' "
            "'exec /opt/moonmind-tools/bin/gh \"$@\"' "
            "> " + _RUNTIME_BIN_DIR + "/gh; "
            "chmod 0700 " + _RUNTIME_BIN_DIR + "/gh; fi; "
            'if [ -n "${MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE:-}" ]; then '
            'test -r "$MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE"; '
            'OMNIGENT_API_TOKEN=$(cat "$MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE"); '
            "export OMNIGENT_API_TOKEN; fi; "
            'exec omnigent host --server "$1" --non-interactive'
        )
        return script, environment


__all__ = ["OmnigentRuntimeScriptService"]
