from moonmind.workflows.speckit_celery.celeryconfig import (
    CODEX_AFFINITY_HEADER,
    CodexShardRouter,
    build_task_router,
    get_codex_shard_router,
)


def test_get_codex_shard_router_uses_single_queue(monkeypatch):
    monkeypatch.setattr(
        "moonmind.workflows.speckit_celery.celeryconfig.settings.spec_workflow.codex_queue",
        "moonmind.jobs",
    )

    router = get_codex_shard_router()

    assert router.shard_count == 1
    assert router.queue_names() == ("moonmind.jobs",)


def test_task_router_routes_codex_tasks_to_fixed_queue():
    router = CodexShardRouter(shard_count=1, codex_queue="moonmind.jobs")
    task_router = build_task_router(router)[0]

    route = task_router(
        "moonmind.workflows.speckit_celery.tasks.submit_codex_job",
        (),
        {},
        {"headers": {CODEX_AFFINITY_HEADER: "abc123"}},
    )

    assert route["queue"] == "moonmind.jobs"
    assert route["routing_key"] == "moonmind.jobs"


def test_task_router_honors_explicit_queue_override():
    router = CodexShardRouter(shard_count=1, codex_queue="moonmind.jobs")
    task_router = build_task_router(router)[0]

    route = task_router(
        "moonmind.workflows.speckit_celery.tasks.submit_codex_job",
        (),
        {},
        {"queue": "explicit-queue", "routing_key": "explicit-routing"},
    )

    assert route == {"queue": "explicit-queue", "routing_key": "explicit-routing"}
