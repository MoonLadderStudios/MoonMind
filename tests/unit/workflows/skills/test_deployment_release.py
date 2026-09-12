import json
from types import SimpleNamespace

import pytest

from moonmind.workflows.skills import deployment_release as release


@pytest.mark.parametrize(
    "children,expected",
    [
        (
            [
                {"ready": True, "buildId": "candidate"},
                {"ready": True, "buildId": "candidate"},
            ],
            True,
        ),
        (
            [
                {"ready": True, "buildId": "candidate"},
                {"ready": True, "buildId": "old"},
            ],
            False,
        ),
        (
            [
                {"ready": True, "buildId": "candidate"},
                {"ready": False, "buildId": "candidate"},
            ],
            False,
        ),
        ([{"ready": True}], False),
        ([], False),
    ],
)
def test_release_qualification_validates_every_supervised_child(children, expected):
    assert (
        release.readiness_matches({"ready": True, "children": children}, "candidate")
        is expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("module_entrypoint", [False, True])
async def test_detached_update_reconciles_lost_launch_ack_and_reuses_terminal_receipt(
    tmp_path, monkeypatch, module_entrypoint
):
    execute = release.execute_detached
    if module_entrypoint:
        import runpy
        import sys

        # Run the actual __main__ branch without dispatching a deployment. Its
        # functions retain __name__ == '__main__', the escaped launcher case.
        with monkeypatch.context() as launch_context:
            launch_context.setattr(
                sys, "argv", [release.__file__, str(tmp_path / "request.json")]
            )
            launch_context.setattr(
                release.asyncio, "run", lambda pending: pending.close()
            )
            namespace = runpy.run_path(release.__file__, run_name="__main__")
        execute = namespace["execute_detached"]
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    owner = "workflow:deployment:step:1"
    digest = "sha256:" + "a" * 64
    inputs = {
        "stack": "moonmind",
        "image": {"repository": "example/moonmind", "reference": "candidate"},
    }
    context = {"idempotency_key": owner, "principal": "system:deployment"}
    container = None
    launches = []

    class Runner:
        async def pull(self, **kwargs):
            return {"exitCode": 0}

        async def inspect_image(self, requested):
            return {"Id": "image-id", "RepoDigests": [f"example/moonmind@{digest}"]}

        async def _run_compose_command(self, command, **kwargs):
            nonlocal container
            launches.append((command, kwargs))
            assert command[-2] == "moonmind.workflows.skills.deployment_release"
            request = release.Path(command[-1])
            record = json.loads(request.read_text())
            assert record["image"] == f"example/moonmind@{digest}"
            release.write_record(
                request.parent / "result.json",
                {
                    "owner": owner,
                    "result": {
                        "status": "COMPLETED",
                        "outputs": {"verified": True},
                        "progress": {},
                    },
                },
            )
            container = {"Image": "image-id", "State": {"Running": False}}
            return {"exitCode": 1, "stderr": "client connection lost after launch"}

    async def inspect(*args):
        return container

    async def docker(*args):
        nonlocal container
        assert args[0] in {"rm", "update"}
        if args[0] == "rm":
            container = None
        return ""

    monkeypatch.setattr(release, "inspect_owned", inspect)
    monkeypatch.setattr(release, "docker", docker)

    async def coherent(*args):
        return {}

    monkeypatch.setattr(release, "require_coherent_images", coherent)
    if module_entrypoint:
        for name, value in (
            ("inspect_owned", inspect),
            ("docker", docker),
            ("require_coherent_images", coherent),
        ):
            monkeypatch.setitem(execute.__globals__, name, value)
    executor = SimpleNamespace(runner=Runner())
    first = await execute(executor, inputs, context)
    second = await execute(executor, inputs, context)
    assert first == second
    assert len(launches) == 1
    changed = {**inputs, "reason": "different authorized operation"}
    with pytest.raises(ValueError, match="different inputs"):
        await execute(executor, changed, context)


@pytest.mark.asyncio
async def test_unreadable_daemon_is_not_an_absent_owner(monkeypatch):
    async def unavailable(*args):
        raise RuntimeError("daemon unavailable")

    monkeypatch.setattr(release, "docker", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        await release.inspect_owned("owned-container", "execution")


@pytest.mark.asyncio
async def test_foreign_container_is_never_adopted(monkeypatch):
    async def daemon(*args):
        if args[0] == "ps":
            return "container-id"
        return json.dumps(
            [{"Config": {"Labels": {"moonmind.release.owner": "another-owner"}}}]
        )

    monkeypatch.setattr(release, "docker", daemon)
    with pytest.raises(ValueError, match="ownership differs"):
        await release.inspect_owned("owned-container", "execution")
