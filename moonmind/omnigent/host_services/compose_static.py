"""Compose packaging for plan-bound static hosts; lifecycle stays generic."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.static_hosts import (
    static_host_row_for_harness,
    static_host_scope_ref,
)


def _unavailable(message):
    raise HarnessPlatformError(
        message, code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED
    )


def deployment_compose_command() -> tuple[str, ...]:
    local_root = Path(
        os.getenv("MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR", "") or Path.cwd()
    ).expanduser()
    host_root = os.getenv("MOONMIND_DEPLOYMENT_PROJECT_DIR", "").strip()
    configured = Path(
        os.getenv("MOONMIND_DEPLOYMENT_COMPOSE_FILE", "docker-compose.yaml")
    ).expanduser()
    if configured.is_absolute() and not configured.exists():
        try:
            relative = (
                configured.relative_to(Path(host_root).expanduser())
                if host_root
                else configured.name
            )
        except ValueError:
            relative = configured.name
        configured = local_root / relative
    elif not configured.is_absolute():
        configured = local_root / configured
    project = (
        os.getenv("MOONMIND_DEPLOYMENT_PROJECT_NAME", "moonmind").strip() or "moonmind"
    )
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", project)
        or not configured.is_file()
    ):
        _unavailable("deployment Compose identity or file is unavailable")
    return (
        "docker",
        "compose",
        "--project-name",
        project,
        "-f",
        str(configured.resolve()),
    )


def compose_service_from_create(argv):
    """Package the shared launch projection without resolving a second config.

    Only flags emitted by the shared renderer are accepted. Compose receives a
    complete service, not an overlay that could inherit ambient credentials.
    """
    if argv[:2] != ["docker", "create"]:
        raise ValueError("expected the shared Docker create projection")
    service = {"environment": {}, "labels": {}, "volumes": [], "networks": []}
    volumes = {}
    networks = {}
    scalar = {
        "--name": "container_name",
        "--hostname": "hostname",
        "--user": "user",
        "--pids-limit": "pids_limit",
        "--memory": "mem_limit",
        "--cpus": "cpus",
        "--entrypoint": "entrypoint",
    }
    repeat = {
        "--cap-drop": "cap_drop",
        "--security-opt": "security_opt",
        "--tmpfs": "tmpfs",
    }
    index = 2
    while index < len(argv) and argv[index].startswith("--"):
        flag = argv[index]
        if flag == "--read-only":
            service["read_only"] = True
            index += 1
            continue
        value = argv[index + 1]
        index += 2
        if flag in scalar:
            service[scalar[flag]] = value
        elif flag in repeat:
            service.setdefault(repeat[flag], []).append(value)
        elif flag in {"--label", "--env"}:
            key, _, item = value.partition("=")
            service["labels" if flag == "--label" else "environment"][key] = item
        elif flag == "--network":
            service["networks"].append(value)
            networks[value] = {"external": True, "name": value}
        elif flag == "--mount":
            parts = dict(
                part.split("=", 1) if "=" in part else (part, True)
                for part in value.split(",")
            )
            mount = {
                "type": parts["type"],
                "source": parts["src"],
                "target": parts["dst"],
                "read_only": parts.get("readonly") is True,
            }
            if "volume-subpath" in parts:
                mount["volume"] = {"subpath": parts["volume-subpath"]}
            if mount["type"] == "volume":
                volumes[mount["source"]] = {"external": True, "name": mount["source"]}
            service["volumes"].append(mount)
        else:
            raise ValueError(f"unsupported shared launch flag {flag}")
    service["image"] = argv[index]
    service["command"] = argv[index + 1 :]
    return service, volumes, networks


def _host_id(value):
    return UUID(str(value).removeprefix("host_")).hex


class ComposeStaticHostService:
    def __init__(self, *, backend, client):
        self._backend = backend
        self._client = client

    async def assert_quiescent(self, host_id):
        """Inspect all native sessions, including archived/sub-agent runners.

        The host catalog's `runners` field is not a liveness inventory. Use the
        session API's explicit runner liveness and fail closed on unknown pages.
        """
        after = None
        seen = set()
        for _ in range(100):
            page = await self._client.list_sessions(agent_id=None, after=after)
            items = page.get("data")
            if not isinstance(items, list) or not isinstance(
                page.get("has_more"), bool
            ):
                _unavailable("static host session inventory is incomplete")
            for item in items:
                if not isinstance(item, dict) or "host_id" not in item:
                    _unavailable("static host session inventory has unknown ownership")
                if item["host_id"] is None:
                    continue
                try:
                    matches = _host_id(item["host_id"]) == _host_id(host_id)
                except ValueError:
                    _unavailable("static host session inventory has invalid ownership")
                if matches and (
                    item.get("runner_online") is not False
                    or item.get("status") not in {"idle", "failed"}
                ):
                    _unavailable("static host still has a native session consumer")
            if not page["has_more"]:
                return
            cursor = items[-1].get("id") if items else None
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                _unavailable("static host session inventory cursor is incomplete")
            seen.add(cursor)
            after = cursor
        _unavailable("static host session inventory exceeded its bounded read")

    async def resolve_spec(self, spec):
        selection = spec.runtime["staticHostSelection"]
        row = static_host_row_for_harness(selection["harness"])
        command = deployment_compose_command()
        _code, raw, _err = await self._backend.run(
            [
                *command,
                "--profile",
                row.compose_profile,
                "config",
                "--format",
                "json",
                row.service,
            ],
            output_limit_bytes=None,
        )
        model = json.loads(raw)
        configured = model["services"][row.service]
        state_mounts = [
            mount
            for mount in configured.get("volumes", [])
            if mount.get("target") == "/home/app/.omnigent"
        ]
        if len(state_mounts) != 1 or state_mounts[0].get("type") != "volume":
            _unavailable("static host state volume is unavailable")
        source = state_mounts[0]["source"]
        state = model["volumes"][source].get("name")
        if not state:
            _unavailable("static host state volume has no concrete identity")
        _code, container_id, _err = await self._backend.run(
            [
                *command,
                "--profile",
                row.compose_profile,
                "ps",
                "--all",
                "-q",
                row.service,
            ]
        )
        ids = container_id.split()
        if len(ids) > 1:
            _unavailable("static Compose service has multiple physical hosts")
        current = None
        if ids:
            _code, raw, _err = await self._backend.run(["docker", "inspect", ids[0]])
            current = json.loads(raw)[0]
            mounts = [
                mount.get("Name")
                for mount in current.get("Mounts", [])
                if mount.get("Destination") == "/home/app/.omnigent"
            ]
            if mounts != [state]:
                _unavailable("static Compose host changed its persistent state")
        name = (
            current["Name"].removeprefix("/")
            if current
            else configured.get("container_name") or f"{command[3]}-{row.service}-1"
        )
        # Read existing identity without creating a config or changing history.
        present, _out, error = await self._backend.run(
            ["docker", "volume", "inspect", "--format", "{{.Name}}", state], check=False
        )
        identity = None
        host_name = None
        if present == 0:
            _code, raw, _err = await self._backend.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--mount",
                    f"type=volume,src={state},dst=/state,readonly",
                    "--entrypoint",
                    "python",
                    spec.imageRef,
                    "-c",
                    "import json; from pathlib import Path; from omnigent.host.identity import load_host_identity_if_present; i=load_host_identity_if_present(Path('/state/config.yaml')); print(json.dumps({'id': i.host_id if i else None, 'name': i.name if i else None}))",
                ]
            )
            identity = json.loads(raw).get("id")
            host_name = json.loads(raw).get("name")
        elif "no such" not in error.lower():
            _unavailable("static host state inspection is unavailable")
        environment = dict(
            item.split("=", 1)
            for item in (current or {}).get("Config", {}).get("Env", [])
            if "=" in item
        )
        identity = environment.get("OMNIGENT_HOST_ID") or identity
        host_name = (
            environment.get("OMNIGENT_HOST_NAME")
            or host_name
            or configured.get("hostname")
            or row.service
        )
        explicit = selection["staticHostId"]
        if explicit and identity and _host_id(explicit) != _host_id(identity):
            _unavailable("static host identity conflicts with its admitted selection")
        scope = static_host_scope_ref(
            harness=selection["harness"],
            endpoint_ref=selection["endpointRef"],
            host_id=explicit,
        )
        identity = _host_id(explicit or identity or uuid5(NAMESPACE_URL, scope))
        await self.assert_quiescent(identity)
        control = dict(
            spec.controlAttachment
            or {
                "kind": "volume",
                "targetPath": "/run/moonmind-host-auth",
                "accessMode": "read-only",
            }
        )
        control["sourceRef"] = (
            "mm-static-control-" + hashlib.sha256(scope.encode()).hexdigest()[:24]
        )
        return spec.model_copy(
            update={
                "correlationName": name,
                "expectedOmnigentHostId": identity,
                "stateAttachment": {**spec.stateAttachment, "sourceRef": state},
                "controlAttachment": control,
                "runtime": {
                    **spec.runtime,
                    "staticComposeService": row.service,
                    "staticComposeProject": command[3],
                    "staticHostName": host_name,
                    "previousGithubVolumes": [
                        mount["Name"]
                        for mount in (current or {}).get("Mounts", [])
                        if mount.get("Destination") == "/run/mm-credentials/github"
                        and mount.get("Name", "").startswith("mm-omnigent-github-")
                    ],
                },
                "labels": {**spec.labels, "moonmind.static_host": "true"},
            }
        )

    async def launch(self, *, spec, command):
        await self.assert_quiescent(spec.expectedOmnigentHostId)
        service, volumes, networks = compose_service_from_create(command)
        service["restart"] = "unless-stopped"
        model = {
            "services": {spec.runtime["staticComposeService"]: service},
            "volumes": volumes,
            "networks": networks,
        }
        # JSON stdin contains paths/configuration only; capability values were
        # delivered to their files by the shared launcher before packaging.
        await self._backend.run(
            [
                "docker",
                "compose",
                "--project-name",
                spec.runtime["staticComposeProject"],
                "-f",
                "-",
                "up",
                "-d",
                "--no-deps",
                spec.runtime["staticComposeService"],
            ],
            input_bytes=json.dumps(model).encode(),
        )
        for volume in spec.runtime.get("previousGithubVolumes", []):
            if volume in volumes:
                continue
            code, owner, _error = await self._backend.run(
                [
                    "docker",
                    "volume",
                    "inspect",
                    "--format",
                    '{{ index .Labels "moonmind.owner" }}',
                    volume,
                ],
                check=False,
            )
            if code == 0 and owner.strip() == "generic-omnigent-github-credential":
                # Only a retired projection with no credential file is
                # removable. An unknown/live projection stays untouched.
                code, _out, _error = await self._backend.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--mount",
                        f"type=volume,src={volume},dst=/projection,readonly",
                        "--entrypoint",
                        "python",
                        service["image"],
                        "-c",
                        "import json; from pathlib import Path; p=Path('/projection'); s=p/'.projection-reservation.json'; assert s.is_file() and not s.is_symlink(); assert json.loads(s.read_text()).get('retired') is True; assert not (p/'hosts.yml').exists()",
                    ],
                    check=False,
                )
                if code == 0:
                    await self._backend.run(
                        ["docker", "volume", "rm", volume], check=False
                    )
        return spec.correlationName
