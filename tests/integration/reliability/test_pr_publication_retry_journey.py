"""Real Temporal retries a lost GitHub create acknowledgment over local HTTP."""
from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
from temporalio import workflow
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.activities.jules_activities import repo_create_pr_activity
from moonmind.workflows.temporal.workflows.run import DEFAULT_ACTIVITY_RETRY_POLICY
from tests.integration.reliability.test_release_routing_journey import connect

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.reliability_journey]


@workflow.defn
class PublicationRetryJourney:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await workflow.execute_activity(
            'repo.create_pr', payload,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )


@pytest.mark.parametrize('rate_limited', [False, True])
async def test_recurring_publication_uses_durable_retry_and_one_remote_create(monkeypatch, rate_limited):
    monkeypatch.setenv('GITHUB_TOKEN', 'github-token-fixture')
    calls = []
    observed_times = []
    created = False
    pr = {'number': 42, 'html_url': 'https://github.com/o/r/pull/42', 'draft': True,
          'head': {'ref': 'feature', 'sha': 'abc123', 'repo': {'full_name': 'o/r'}},
          'base': {'ref': 'main', 'repo': {'full_name': 'o/r'}}}

    class GitHub(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            nonlocal created
            calls.append(self.command)
            observed_times.append(time.monotonic())
            assert self.headers['Authorization'] == 'Bearer github-token-fixture'
            if self.command == 'GET':
                status, payload = 200, [pr] if created else []
            elif self.command == 'POST':
                assert not created
                created = True
                status, payload = (403 if rate_limited else 500), {'message': 'lost acknowledgment'}
            else:
                assert self.command == 'PATCH'
                status, payload = 200, pr
            body = json.dumps(payload).encode()
            self.send_response(status)
            if rate_limited and status == 403:
                self.send_header('Retry-After', '1')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PATCH = handle_request

    server = ThreadingHTTPServer(('127.0.0.1', 0), GitHub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client_class = httpx.AsyncClient

    class LocalGitHub(httpx.AsyncBaseTransport):
        def __init__(self):
            self.transport = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            assert request.url.host == 'api.github.com'
            request.url = request.url.copy_with(scheme='http', host='127.0.0.1', port=server.server_port)
            return await self.transport.handle_async_request(request)

        async def aclose(self):
            await self.transport.aclose()

    monkeypatch.setattr('moonmind.workflows.adapters.github_service.httpx.AsyncClient',
                        lambda **kwargs: client_class(transport=LocalGitHub(), **kwargs))
    try:
        client = await connect()
        queue = 'publication-retry-' + uuid4().hex
        async with Worker(client, task_queue=queue, workflows=[PublicationRetryJourney],
                          activities=[repo_create_pr_activity], workflow_runner=UnsandboxedWorkflowRunner()):
            result = await client.execute_workflow(
                PublicationRetryJourney.run,
                {'repo': 'o/r', 'head': 'feature', 'base': 'main',
                 'title': 'Saved candidate', 'body': 'Remaining work', 'draft': True},
                id=queue, task_queue=queue, execution_timeout=timedelta(minutes=2),
            )
        assert result['adopted'] and result['url'] == pr['html_url']
        assert calls == ['GET', 'POST', 'GET', 'PATCH']
        if rate_limited:
            assert observed_times[2] - observed_times[1] >= 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
