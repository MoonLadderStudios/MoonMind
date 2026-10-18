import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';

import type { BootPayload } from '../boot/parseBootPayload';
import { createDashboardQueryClient } from '../boot/queryClient';
import { renderWithClient } from '../utils/test-utils';
import { WorkflowDetailPage } from './workflow-detail';

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: { data?: unknown[]; itemContent: (index: number, row: unknown) => ReactNode }) => (
    <div>{(props.data ?? []).map((row, index) => <div key={index}>{props.itemContent(index, row)}</div>)}</div>
  ),
}));

vi.mock('../lib/navigation', () => ({
  navigateTo: vi.fn(),
}));

const DETAIL_URL = '/api/executions/test-123?source=temporal';
const STEPS_URL = '/api/executions/test-123/steps';

function jsonResponse(body: unknown, status = 200, statusText = 'OK'): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText,
    headers: { 'Content-Type': 'application/json' },
  });
}

function detailPayload(detailPoll = 60_000, temporalDashboard: Record<string, unknown> = {}): BootPayload {
  return {
    page: 'workflow-detail',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        pollIntervalsMs: { detail: detailPoll, list: 60_000 },
        features: { temporalDashboard },
      },
    },
  };
}

const baseExecution = {
  taskId: 'test-123',
  workflowId: 'test-123',
  namespace: 'default',
  temporalRunId: '02-run',
  runId: '02-run',
  source: 'temporal',
  workflowType: 'MoonMind.UserWorkflow',
  title: 'Cached workflow title',
  summary: 'Known summary',
  status: 'running',
  state: 'executing',
  rawState: 'executing',
  temporalStatus: 'running',
  createdAt: '2026-04-09T00:00:00Z',
  startedAt: '2026-04-09T00:00:00Z',
  updatedAt: '2026-04-09T00:00:04Z',
  actions: {},
  relatedRuns: [],
};

function stepRow(overrides: Record<string, unknown>) {
  return {
    logicalStepId: 'plan',
    order: 1,
    title: 'Plan work',
    tool: { type: 'skill', name: 'plan.generate', version: '1' },
    dependsOn: [],
    status: 'completed',
    waitingReason: null,
    attentionRequired: false,
    executionOrdinal: 1,
    startedAt: '2026-04-09T00:00:01Z',
    updatedAt: '2026-04-09T00:00:02Z',
    summary: null,
    checks: [],
    refs: { childWorkflowId: null, childRunId: null, agentRunId: null },
    artifacts: {},
    lastError: null,
    ...overrides,
  };
}

const stepsSnapshot = {
  workflowId: 'test-123',
  runId: '02-run',
  runScope: 'latest',
  steps: [
    stepRow({
      timing: {
        startedAt: '2026-04-09T00:00:01Z',
        endedAt: '2026-04-09T00:00:02Z',
        durationMs: 1000,
        elapsedMs: 1000,
        serverNow: '2026-04-09T00:00:02Z',
        precision: 'exact',
        preserved: false,
      },
    }),
    stepRow({
      logicalStepId: 'apply',
      order: 2,
      title: 'Apply patch',
      dependsOn: ['plan'],
      status: 'executing',
      startedAt: '2026-04-09T00:00:03Z',
      updatedAt: '2026-04-09T00:00:38Z',
      // The row was last written 35 s after it started; the server's elapsed
      // figure is as of that write, not as of now.
      timing: {
        startedAt: '2026-04-09T00:00:03Z',
        endedAt: null,
        durationMs: null,
        elapsedMs: 35_000,
        serverNow: '2026-04-09T00:00:38Z',
        precision: 'live',
        preserved: false,
      },
    }),
  ],
};

let fetchSpy: MockInstance;

beforeEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
  window.localStorage.clear();
  fetchSpy = vi.spyOn(window, 'fetch');
});

afterEach(() => {
  vi.useRealTimers();
});

function routeFetch(routes: Record<string, () => Response>) {
  fetchSpy.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    const handler = routes[url];
    if (handler) return Promise.resolve(handler());
    if (url.endsWith('/checkpoint-branches')) return Promise.resolve(jsonResponse({ items: [] }));
    return Promise.resolve(jsonResponse({ artifacts: [], items: [] }));
  });
}

async function staleNotice(): Promise<HTMLElement> {
  const text = await screen.findByText(/Stale since/);
  return text.closest('.notice') as HTMLElement;
}

describe('workflow detail keeps known data when a refresh fails', () => {
  it('keeps the cached workflow visible with a stale notice and retries on request', async () => {
    window.history.pushState({}, '', '/workflows/test-123/overview?source=temporal');
    let failDetail = false;
    let detailCalls = 0;
    routeFetch({
      [DETAIL_URL]: () => {
        detailCalls += 1;
        return failDetail
          ? jsonResponse({ detail: 'Temporal is restarting' }, 503, 'Service Unavailable')
          : jsonResponse(baseExecution);
      },
    });

    const { queryClient } = renderWithClient(<WorkflowDetailPage payload={detailPayload()} />);
    expect(await screen.findByRole('heading', { name: 'Cached workflow title' })).toBeTruthy();

    failDetail = true;
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['workflow-detail'] });
    });

    const notice = await staleNotice();
    expect(notice.textContent).toContain('Temporal is restarting');
    expect(screen.getByRole('heading', { name: 'Cached workflow title' })).toBeTruthy();
    expect(screen.getByText('Known summary')).toBeTruthy();

    failDetail = false;
    const callsBeforeRetry = detailCalls;
    fireEvent.click(within(notice).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText(/Stale since/)).toBeNull());
    expect(detailCalls).toBe(callsBeforeRetry + 1);
    expect(screen.getByRole('heading', { name: 'Cached workflow title' })).toBeTruthy();
  });

  it('keeps the step list visible with a stale notice when the steps refresh fails', async () => {
    window.history.pushState({}, '', '/workflows/test-123/steps?source=temporal');
    let failSteps = false;
    routeFetch({
      [DETAIL_URL]: () => jsonResponse({ ...baseExecution, stepsHref: STEPS_URL }),
      [STEPS_URL]: () => (failSteps
        ? jsonResponse({ detail: 'Workflow query timed out' }, 504, 'Gateway Timeout')
        : jsonResponse(stepsSnapshot)),
    });

    const { queryClient } = renderWithClient(<WorkflowDetailPage payload={detailPayload()} />);
    expect((await screen.findAllByText('Plan work')).length).toBeGreaterThan(0);

    failSteps = true;
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['workflow-detail-steps'] });
    });

    const notice = await staleNotice();
    expect(notice.textContent).toContain('Workflow query timed out');
    expect(screen.getAllByText('Plan work').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Apply patch').length).toBeGreaterThan(0);
  });

  it('keeps the artifact browser rows visible with a stale notice when the artifacts refresh fails', async () => {
    window.history.pushState({}, '', '/workflows/test-123/artifacts?source=temporal');
    let failArtifacts = false;
    routeFetch({
      [DETAIL_URL]: () => jsonResponse(baseExecution),
      '/api/executions/default/test-123/02-run/artifacts': () => (failArtifacts
        ? jsonResponse({ detail: 'Artifact store unavailable' }, 503, 'Service Unavailable')
        : jsonResponse({
          artifacts: [{
            artifactId: 'art-browser-1',
            contentType: 'text/plain',
            sizeBytes: 12,
            status: 'complete',
            metadata: { filename: 'notes.txt' },
            links: [],
          }],
        })),
    });

    const { queryClient } = renderWithClient(<WorkflowDetailPage payload={detailPayload()} />);
    expect((await screen.findAllByText('notes.txt')).length).toBeGreaterThan(0);

    failArtifacts = true;
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['workflow-detail-artifacts'] });
    });

    const notice = await staleNotice();
    expect(notice.textContent).toContain('Artifact store unavailable');
    expect(screen.getAllByText('notes.txt').length).toBeGreaterThan(0);
  });
});

describe('workflow detail load failures', () => {
  it('shows a not-found state for a missing workflow without retrying or polling it', async () => {
    window.history.pushState({}, '', '/workflows/test-123/overview?source=temporal');
    let detailCalls = 0;
    routeFetch({
      [DETAIL_URL]: () => {
        detailCalls += 1;
        return jsonResponse({ detail: 'Workflow test-123 was not found.' }, 404, 'Not Found');
      },
    });

    const queryClient = createDashboardQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <WorkflowDetailPage payload={detailPayload(100)} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('heading', { name: 'Workflow not found' })).toBeTruthy();
    expect(screen.getByText(/Workflow test-123 was not found\./)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();

    // The shared policy would retry a transient failure after 1 s, and the
    // detail poll runs every 100 ms; a missing workflow must do neither.
    await new Promise((resolve) => setTimeout(resolve, 1300));
    expect(detailCalls).toBe(1);
    queryClient.clear();
  });

  it.each([
    [401, 'Unauthorized', 'Sign-in required'],
    [403, 'Forbidden', 'Access denied'],
  ])('shows an access state for HTTP %i', async (status, statusText, heading) => {
    window.history.pushState({}, '', '/workflows/test-123/overview?source=temporal');
    routeFetch({
      [DETAIL_URL]: () => jsonResponse({ detail: 'Not allowed.' }, status, statusText),
    });

    renderWithClient(<WorkflowDetailPage payload={detailPayload()} />);

    expect(await screen.findByRole('heading', { name: heading })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Cached workflow title' })).toBeNull();
  });

  it('shows an unavailable state with a retry that loads the workflow once the API recovers', async () => {
    window.history.pushState({}, '', '/workflows/test-123/overview?source=temporal');
    let available = false;
    routeFetch({
      [DETAIL_URL]: () => (available
        ? jsonResponse(baseExecution)
        : jsonResponse({ detail: 'Upstream restarting' }, 502, 'Bad Gateway')),
    });

    renderWithClient(<WorkflowDetailPage payload={detailPayload()} />);

    expect(await screen.findByRole('heading', { name: 'Workflow unavailable' })).toBeTruthy();
    expect(screen.getByText(/Upstream restarting/)).toBeTruthy();

    available = true;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('heading', { name: 'Cached workflow title' })).toBeTruthy();
  });
});
