"""Disposable Compose upgrade through the selected production controller.

The same operation and lock own image staging, recreation, conversion and access
verification. The adapter adds only isolated test-target admission and diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import re
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


_redactor_spec = importlib.util.spec_from_file_location(
    "journey_artifact_redactor", Path(__file__).with_name("ci") / "redact_diagnostics.py"
)
artifact_redactor = importlib.util.module_from_spec(_redactor_spec)
_redactor_spec.loader.exec_module(artifact_redactor)


class DiagnosticRunner:
    """Observe a failed disposable helper without replacing or retrying it."""

    def __init__(self, runner, *, controller, path, project, operation_id):
        self.runner = runner
        self.controller = controller
        self.path = Path(path)
        self.project = project
        self.operation_id = operation_id
        self.failure = None

    def _text(self, value):
        # Never serialize argv, request payloads, environment, or full inspect.
        redacted, _count = artifact_redactor.redact_text(str(value or ""))
        return self.controller.redact_text(redacted)[-4000:]

    def _save(self):
        self.controller.record_mod._atomic_write_json(self.path, self.failure)

    def _capture(self, error):
        self.failure = {"firstFailure": {
            "phase": "routing-cutover-apply",
            "error": type(error).__name__,
            "exitStatus": getattr(error, "exit_status", None),
            "output": self._text(getattr(error, "output", "")),
        }, "helpers": {}}
        try:
            # Save the first error before any subsequent Docker read can fail.
            self._save()
            deadline = time.monotonic() + 30

            def observe(args, *, combined=False):
                remaining = int(deadline - time.monotonic())
                if remaining <= 0:
                    raise TimeoutError("diagnostic budget exhausted")
                result = self.controller.engine.run_command(
                    self.runner, args, timeout_seconds=remaining
                )
                return (
                    str(result.get("output") or "") if combined
                    else self.controller.engine.command_stdout(result)
                )

            listed = observe((
                "docker", "ps", "--all", "--filter",
                f"label={self.controller.OMNIGENT_OPERATION_LABEL}={self.operation_id}",
                "--filter", f"label=com.docker.compose.project={self.project}",
                "--filter", "label=com.docker.compose.oneoff=True", "--format", "{{.ID}}",
            )).split()
            if any(not re.fullmatch(r"[a-f0-9]{12,64}", item) for item in listed):
                raise ValueError("invalid helper inventory")
            self.failure["omittedHelpers"] = max(0, len(listed) - 4)
            for identifier in listed[:4]:
                helper = self.failure["helpers"][identifier] = {}
                helper["state"] = self._text(observe((
                    "docker", "inspect", "--format",
                    '{"running":{{json .State.Running}},'
                    '"status":{{json .State.Status}},"exitCode":{{json .State.ExitCode}},'
                    '"startedAt":{{json .State.StartedAt}},'
                    '"finishedAt":{{json .State.FinishedAt}}}',
                    identifier,
                )))
                self._save()
                helper["logs"] = self._text(observe((
                    "docker", "logs", "--tail", "100", identifier,
                ), combined=True))
                self._save()
        except Exception as exc:
            # Exception text may contain argv or configuration; keep only type.
            self.failure["collectionError"] = type(exc).__name__
            try:
                self._save()
            except OSError:
                # Diagnostic persistence is best effort; preserve the original
                # command failure even when its diagnostic file cannot be written.
                pass

    def run(self, args, timeout_seconds):
        def option(flag):
            if args.count(flag) != 1:
                return None
            index = args.index(flag) + 1
            return args[index] if index < len(args) else None

        helper = (
            tuple(args[:2]) == ("docker", "compose")
            and option("--project-name") == self.project
            and option("--label") == (
                f"{self.controller.OMNIGENT_OPERATION_LABEL}={self.operation_id}"
            )
            and option("--entrypoint") == "python"
            and self.controller.LEGACY_CONTROL_SERVICE in args
            and "-c" in args
            and args.index("-c") + 1 < len(args)
            and "run" in args
        )
        if helper:
            # This diagnostic wrapper exists only in the disposable journey.
            # One stack snapshot can distinguish an RPC wait from interpreter
            # shutdown; it neither terminates nor signals the running helper.
            args = list(args)
            index = args.index("-c") + 1
            args[index] = (
                "import faulthandler,sys\n"
                "try:\n"
                "    faulthandler.dump_traceback_later(120, repeat=False, exit=False)\n"
                "except Exception:\n"
                "    print('MOONMIND_CUTOVER_HELPER=stack-unavailable', file=sys.stderr, flush=True)\n"
                "print('MOONMIND_CUTOVER_HELPER=python-started', file=sys.stderr, flush=True)\n"
                "try:\n"
                f"    exec({args[index]!r}, globals())\n"
                "except SystemExit:\n"
                "    print('MOONMIND_CUTOVER_HELPER=launcher-exited', file=sys.stderr, flush=True)\n"
                "    raise\n"
            )
            args = tuple(args)
        try:
            return self.runner.run(args, timeout_seconds)
        except Exception as exc:
            if helper and self.failure is None:
                try:
                    self._capture(exc)
                except Exception:
                    pass  # Diagnostics never replace the original failure.
            raise


def validate_target(*, repo, project, image, compose_files):
    if not re.fullmatch(r"moonmind-test(?:-[a-z0-9][a-z0-9_-]*)?", project):
        raise ValueError("cutover requires an isolated moonmind-test project")
    if not re.fullmatch(
        r"(?:sha256:[0-9a-f]{64}|127\.0\.0\.1:[0-9]+/moonmind:journey)", image
    ):
        raise ValueError(
            "upgrade requires its inspected image ID or isolated registry image"
        )
    repo = Path(repo).resolve()
    marker = repo / ".first-run-journey-project"
    if (
        not repo.name.startswith("moonmind-upgrade-4356.")
        or not (repo / ".git").is_file()
        or not marker.is_file()
        or marker.read_text().strip() != project
    ):
        raise ValueError("cutover requires its owned disposable upgrade checkout")
    files = [Path(path).resolve() for path in compose_files]
    if (
        not files
        or files[0] != repo / "docker-compose.yaml"
        or not all(path.is_file() for path in files)
    ):
        raise ValueError("cutover requires the existing selected Compose files")
    return repo, [str(path) for path in files]


def run_cutover(
    *,
    repo,
    project,
    image,
    revision,
    compose_files,
    controller,
    diagnostics_path=None,
):
    repo, compose_files = validate_target(
        repo=repo, project=project, image=image, compose_files=compose_files
    )
    state = repo / "deploy/state/controller"
    target = {
        "project": project,
        "projectDir": str(repo),
        "composeFiles": list(compose_files),
    }
    identity = hashlib.sha256(
        json.dumps([project, image, revision, target], sort_keys=True).encode()
    ).hexdigest()[:24]
    operation_id = f"journey-cutover-{identity}"
    store = controller.record_mod.OperationStore(state)
    runner = controller.engine.subprocess_runner()

    def resolve_target(_stack):
        base = controller.engine.compose_base(
            project=project,
            project_dir=str(repo),
            compose_files=compose_files,
        )
        rendered = controller.engine.run_command(
            runner,
            (*base, "config", "--services"),
            timeout_seconds=controller.TARGET_CONFIG_TIMEOUT_SECONDS,
        )
        services = [
            name
            for name in controller.engine.command_stdout(rendered).split()
            if name not in controller.DEFAULT_EXCLUDED_SERVICES
        ]
        if int(rendered.get("exit", 0)) or not services:
            raise RuntimeError("the disposable upgrade renders no services")
        return {**target, "services": services}

    if diagnostics_path is not None:
        runner = DiagnosticRunner(
            runner,
            controller=controller,
            path=diagnostics_path,
            project=project,
            operation_id=operation_id,
        )
    original_runner = controller.engine.subprocess_runner
    controller.engine.subprocess_runner = lambda: runner
    try:
        # Admission, serialization and the stack lock belong to the WSGI
        # owner. Never hand-create an accepted operation in a journey.
        secret = "disposable-cutover-secret"
        app = controller.build_app(
            store=store,
            secret=secret,
            target_resolver=resolve_target,
            legacy_writer_probe=controller.reconcile_retired_shell_controller,
        )
        body = json.dumps(
            {
                "stack": project,
                "desiredImage": image,
                "sourceRevision": revision,
                "operationId": operation_id,
                "reason": "Disposable Compose upgrade through the production controller",
            }
        ).encode()
        status = []
        response = b"".join(
            app(
                {
                    "REQUEST_METHOD": "POST",
                    "PATH_INFO": "/v1/operations",
                    "CONTENT_LENGTH": str(len(body)),
                    "wsgi.input": io.BytesIO(body),
                    "HTTP_AUTHORIZATION": f"Bearer {secret}",
                },
                lambda value, _headers: status.append(value),
            )
        )
        if status != ["202 Accepted"]:
            raise RuntimeError(
                f"controller submission refused: {status}: {response.decode()}"
            )
        final = store.load(operation_id)
        if (
            any(final["target"].get(key) != value for key, value in target.items())
            or final["desired"]["image"] != image
        ):
            raise ValueError("existing upgrade operation targets another deployment")
        return {
            "passed": final.get("status") == "succeeded",
            "receipt": store.routing_cutover(stack=project),
            "operation": final,
        }
    finally:
        controller.engine.subprocess_runner = original_runner


def operator_health(api_base):
    # Reach the published operator URL, independently of container health.
    with build_opener(ProxyHandler({})).open(
        api_base + "/healthz", timeout=10
    ) as response:
        return response.status


def run_recovery_cutover(*, api_base, **kwargs):
    """Cross pull refusal, lost acknowledgment, restart and real delayed startup.

    The upgrade lane holds API startup at a disposable process gate. Opening
    that gate permits the ordinary entrypoint and healthcheck to run; it never
    supplies a health response or writes a controller completion record.
    """
    controller = kwargs["controller"]
    repo, files = validate_target(
        repo=kwargs["repo"],
        project=kwargs["project"],
        image=kwargs["image"],
        compose_files=kwargs["compose_files"],
    )
    project = kwargs["project"]
    base = controller.engine.compose_base(
        project=project,
        project_dir=str(repo),
        compose_files=files,
    )
    original_runner = controller.engine.subprocess_runner
    original_wait = controller.engine.wait_for_services
    runner = original_runner()
    recovery = {
        "apply_count": 0,
        "pull_refusals": 0,
        "resumed_once": False,
        "delayed_api_observed": False,
        "workers_preserved_on_pull_failure": False,
    }

    def snapshot():
        result = controller.engine.run_command(
            runner,
            (*base, "ps", "--all", "--format", "json"),
            timeout_seconds=30,
        )
        raw = controller.engine.command_stdout(result).strip()
        rows = (
            json.loads(raw)
            if raw.startswith("[")
            else [json.loads(line) for line in raw.splitlines()]
        )
        return {
            row["Service"]: {key: row.get(key) for key in ("ID", "State", "Health")}
            for row in rows
            if not controller.engine._is_one_off(row)
        }

    before = snapshot()
    workers = {
        name: row for name, row in before.items() if name.startswith("temporal-worker-")
    }
    assert workers and all(
        row["State"] == "running" for row in workers.values()
    ), before
    assert operator_health(api_base) == 200

    class Interrupted(BaseException):
        pass

    class FaultRunner:
        def run(self, args, timeout_seconds):
            if tuple(args[:2]) == ("docker", "compose"):
                if "pull" in args and recovery["pull_refusals"] == 0:
                    after = snapshot()
                    assert {name: after[name] for name in workers} == workers
                    assert operator_health(api_base) == 200
                    recovery["workers_preserved_on_pull_failure"] = True
                    recovery["pull_refusals"] += 1
                    # Production staging records this original failure and
                    # uses its ordinary bounded retry before any mutation.
                    raise controller.engine.CommandError(
                        "pull", 75, "controlled registry unavailable before mutation"
                    )
            result = runner.run(args, timeout_seconds)
            # Journal readiness uses up --no-recreate before application
            # recreation. Count only successful recreations as confirmed applies.
            if (
                tuple(args[:2]) == ("docker", "compose")
                and "up" in args
                and "--no-recreate" not in args
                and int(result.get("exit", 0)) == 0
            ):
                recovery["apply_count"] += 1
            return result

    fault_runner = FaultRunner()

    def interrupt_readiness(current_runner, current_base, services, **options):
        observed = controller.engine.observe_services(
            current_runner, current_base, services, timeout_seconds=30
        )
        assert observed["services"].get("api") is False, observed
        recovery["delayed_api_observed"] = True
        raise Interrupted(
            "controller lost after confirmed apply, before acknowledgment"
        )

    controller.engine.subprocess_runner = lambda: fault_runner
    controller.engine.wait_for_services = interrupt_readiness
    try:
        try:
            run_cutover(**kwargs)
        except Interrupted:
            pass
        else:
            raise AssertionError(
                "upgrade did not cross the interrupted readiness boundary"
            )
        # A new observer opens the same durable records, as controller startup
        # does. No accepted operation, installed checkpoint or ready row is seeded.
        store = controller.record_mod.OperationStore(repo / "deploy/state/controller")
        (operation,) = store.list_open(stack=project)
        operation_id = operation["operationId"]
        assert operation["installed"]["image"] == kwargs["image"]
        installed = snapshot()
        assert recovery["apply_count"] == 1
        controller.engine.wait_for_services = original_wait
        controller.engine.run_command(
            runner,
            (*base, "exec", "-T", "api", "touch", "/tmp/moonmind-journey-start"),
            timeout_seconds=30,
        )
        resumed = controller.resume_open_operations(
            store,
            lambda current: controller.production_apply(store, current),
            legacy_writer_probe=controller.reconcile_retired_shell_controller,
            runner=fault_runner,
        )
        again = controller.resume_open_operations(
            store,
            lambda current: controller.production_apply(store, current),
            legacy_writer_probe=controller.reconcile_retired_shell_controller,
            runner=fault_runner,
        )
        recovery["resumed_once"] = (
            resumed["converged"] == [operation_id] and again["converged"] == []
        )
        assert recovery["resumed_once"], (resumed, again)
        # Redelivery after the missing acknowledgment uses the original identity.
        result = run_cutover(**kwargs)
        assert result["operation"]["operationId"] == operation_id
        assert result["passed"], result
        assert recovery["apply_count"] == 1
        after = snapshot()
        assert {name: after[name]["ID"] for name in ("api", *workers)} == {
            name: installed[name]["ID"] for name in ("api", *workers)
        }
        assert all(
            after[name]["State"] == "running"
            and after[name]["Health"] in ("", "healthy")
            for name in workers
        )
        assert operator_health(api_base) == 200
        assert (
            "registry unavailable before mutation"
            in result["operation"]["errorSummary"]
        )
        result["recovery"] = recovery
        return result
    finally:
        controller.engine.subprocess_runner = original_runner
        controller.engine.wait_for_services = original_wait
        # Preserve already observed effects even if later verification fails.
        if kwargs.get("diagnostics_path") is not None:
            try:
                controller.record_mod._atomic_write_json(
                    Path(kwargs["diagnostics_path"]).with_name("upgrade-recovery.json"),
                    recovery,
                )
            except OSError as exc:
                print(
                    f"Recovery evidence write failed: {type(exc).__name__}",
                    file=sys.stderr,
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--receipt-file", type=Path, required=True)
    parser.add_argument("-f", "--compose-file", action="append", required=True)
    args = parser.parse_args()
    validate_target(
        repo=args.repo,
        project=args.project,
        image=args.image,
        compose_files=args.compose_file,
    )
    # Resolve the actual selected checkout's controller, never the caller's.
    sys.path.insert(0, str(args.repo.resolve() / "deploy/controller"))
    import server as controller

    try:
        result = run_recovery_cutover(
            api_base=args.api_base,
            repo=args.repo,
            project=args.project,
            image=args.image,
            revision=args.revision,
            compose_files=args.compose_file,
            controller=controller,
            diagnostics_path=args.receipt_file.with_name(
                args.receipt_file.stem + "-diagnostics.json"
            ),
        )
    except Exception as exc:
        result = {"passed": False, "error": controller.redact_text(str(exc))}
    args.receipt_file.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_file.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
