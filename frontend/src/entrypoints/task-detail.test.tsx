import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, act, fireEvent } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import { EXECUTING_STATUS_PILL_TRACEABILITY } from '../utils/executionStatusPillClasses';
import {
  expandRouteTemplate,
  getSessionProjectionRefetchInterval,
  normalizeObservabilityEvent,
  TaskDetailPage,
} from './task-detail';
import { taskEditHref, taskRerunHref } from '../lib/temporalTaskEditing';
import { BootPayload } from '../boot/parseBootPayload';
import { MockInstance } from 'vitest';

type MockVirtuosoRow = { id: string };
type MockVirtuosoProps<Row = MockVirtuosoRow> = {
  data?: Row[];
  computeItemKey?: (index: number, row: Row) => string;
  itemContent: (index: number, row: Row) => ReactNode;
  initialItemCount?: number;
};

const { virtuosoPropsSpy } = vi.hoisted(() => ({
  virtuosoPropsSpy: vi.fn(),
}));

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: MockVirtuosoProps) => {
    virtuosoPropsSpy(props);
    return (
      <div data-testid="mock-virtuoso">
        {(props.data ?? []).map((row, index) => {
          const key = props.computeItemKey ? props.computeItemKey(index, row) : index;
          return <div key={String(key)}>{props.itemContent(index, row)}</div>;
        })}
      </div>
    );
  },
}));

// ---------------------------------------------------------------------------
// Minimal EventSource mock
// ---------------------------------------------------------------------------

type LogChunkListener = (event: MessageEvent) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];
  static reset() {
    MockEventSource.instances = [];
  }

  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  private listeners = new Map<string, LogChunkListener[]>();
  closed = false;

  constructor(public url: string, public options?: EventSourceInit) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: LogChunkListener) {
    const existing = this.listeners.get(type) ?? [];
    this.listeners.set(type, [...existing, listener]);
  }

  close() {
    this.closed = true;
  }

  // Test helpers
  triggerOpen() {
    this.onopen?.(new Event('open'));
  }

  triggerLogChunk(
    data: Record<string, unknown> & { sequence: number; stream: string; text: string; timestamp?: string; kind?: string },
  ) {
    const event = new MessageEvent('log_chunk', {
      data: JSON.stringify({
        timestamp: '2026-04-08T00:00:00Z',
        ...data,
      }),
    });
    for (const listener of this.listeners.get('log_chunk') ?? []) listener(event);
  }

  triggerMessage(
    data: Record<string, unknown> & { sequence: number; stream: string; text: string; timestamp?: string; kind?: string },
  ) {
    this.onmessage?.(
      new MessageEvent('message', {
        data: JSON.stringify({
          timestamp: '2026-04-08T00:00:00Z',
          ...data,
        }),
      }),
    );
  }

  triggerError() {
    this.onerror?.(new Event('error'));
  }
}

async function waitForEventSourceInstance() {
  await waitFor(
    () => expect(MockEventSource.instances.length).toBeGreaterThan(0),
    { timeout: 5000 },
  );
}

describe('Task Detail Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
  };
  const actionsPayload: BootPayload = {
    ...mockPayload,
    initialData: {
      dashboardConfig: {
        features: {
          temporalDashboard: {
            actionsEnabled: true,
          },
        },
      },
    },
  };
  const stepsPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        pollIntervalsMs: { detail: 1 },
        sources: {
          taskRuns: {
            observabilitySummary: '/api/task-runs/{taskRunId}/observability-summary',
            observabilityEvents: '/api/task-runs/{taskRunId}/observability/events',
            logsStream: '/api/task-runs/{taskRunId}/logs/stream',
            logsStdout: '/api/task-runs/{taskRunId}/logs/stdout',
            logsStderr: '/api/task-runs/{taskRunId}/logs/stderr',
            logsMerged: '/api/task-runs/{taskRunId}/logs/merged',
            diagnostics: '/api/task-runs/{taskRunId}/diagnostics',
            artifactSession: '/api/task-runs/{taskRunId}/artifact-sessions/{sessionId}',
            artifactSessionControl: '/api/task-runs/{taskRunId}/artifact-sessions/{sessionId}/control',
          },
        },
      },
    },
  };

  const latestStepsSnapshot = {
    workflowId: 'test-123',
    runId: '02-run',
    runScope: 'latest',
    steps: [
      {
        logicalStepId: 'plan',
        order: 1,
        title: 'Plan work',
        tool: { type: 'skill', name: 'plan.generate', version: '1' },
        dependsOn: [],
        status: 'succeeded',
        waitingReason: null,
        attentionRequired: false,
        attempt: 1,
        startedAt: '2026-04-09T00:00:01Z',
        updatedAt: '2026-04-09T00:00:02Z',
        summary: 'Plan complete',
        checks: [],
        refs: { childWorkflowId: null, childRunId: null, taskRunId: null },
        artifacts: {
          outputSummary: 'art-step-plan',
          outputPrimary: null,
          runtimeStdout: null,
          runtimeStderr: null,
          runtimeMergedLogs: null,
          runtimeDiagnostics: null,
          providerSnapshot: null,
        },
        lastError: null,
      },
      {
        logicalStepId: 'apply',
        order: 2,
        title: 'Apply patch',
        tool: { type: 'agent_runtime', name: 'codex_cli', version: '1' },
        dependsOn: ['plan'],
        status: 'running',
        waitingReason: null,
        attentionRequired: false,
        attempt: 1,
        startedAt: '2026-04-09T00:00:03Z',
        updatedAt: '2026-04-09T00:00:04Z',
        summary: 'Applying repository changes',
        checks: [
          {
            kind: 'approval_policy',
            status: 'passed',
            summary: 'Auto-approved',
            retryCount: 0,
            artifactRef: null,
          },
        ],
        refs: {
          childWorkflowId: 'child-wf-1',
          childRunId: 'child-run-1',
          taskRunId: 'task-run-step-1',
        },
        artifacts: {
          outputSummary: 'art-step-summary',
          outputPrimary: 'art-step-output',
          runtimeStdout: null,
          runtimeStderr: null,
          runtimeMergedLogs: null,
          runtimeDiagnostics: 'art-step-diagnostics',
          providerSnapshot: null,
        },
        lastError: null,
      },
      {
        logicalStepId: 'verify',
        order: 3,
        title: 'Verify tests',
        tool: { type: 'skill', name: 'repo.run_tests', version: '1' },
        dependsOn: ['apply'],
        status: 'ready',
        waitingReason: null,
        attentionRequired: false,
        attempt: 0,
        startedAt: null,
        updatedAt: '2026-04-09T00:00:04Z',
        summary: 'Ready to start',
        checks: [],
        refs: { childWorkflowId: null, childRunId: null, taskRunId: null },
        artifacts: {
          outputSummary: null,
          outputPrimary: null,
          runtimeStdout: null,
          runtimeStderr: null,
          runtimeMergedLogs: null,
          runtimeDiagnostics: null,
          providerSnapshot: null,
        },
        lastError: null,
      },
    ],
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    vi.restoreAllMocks();
    virtuosoPropsSpy.mockClear();
    window.history.pushState({}, 'Test', '/tasks/test-123?source=temporal');
    window.sessionStorage.clear();
    fetchSpy = vi.spyOn(window, 'fetch');
    fetchSpy.mockClear();
  });

  it('returns null for route templates with missing parameters', () => {
    expect(
      expandRouteTemplate('/api/task-runs/{taskRunId}/artifact-sessions/{sessionId}', {
        taskRunId: 'task-run-1',
        sessionId: null,
      }),
    ).toBeNull();
    expect(
      expandRouteTemplate('/api/task-runs/{taskRunId}/artifact-sessions/{sessionId}', {
        taskRunId: 'task-run-1',
      }),
    ).toBeNull();
  });

  it('renders a Steps section above Timeline and Artifacts and loads steps before execution-wide artifacts', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Step detail task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({
          ok: true,
          json: async () => latestStepsSnapshot,
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Steps' })).toBeTruthy();
      expect(screen.getByText('Plan work')).toBeTruthy();
      expect(screen.getByText('Apply patch')).toBeTruthy();
      expect(screen.getByText('Verify tests')).toBeTruthy();
      expect(screen.getByText('Merge Automation').closest('div')?.textContent).toContain('—');
      expect(screen.getByText(/^Latest Run:?$/)).toBeTruthy();
      expect(screen.getAllByText('02-run').length).toBeGreaterThan(0);
    });

    const stepsHeading = screen.getByRole('heading', { name: 'Steps' });
    const timelineHeading = screen.getByRole('heading', { name: 'Timeline' });
    const artifactsHeading = screen.getByRole('heading', { name: 'Artifacts' });

    const positions: [number, number] = [
      stepsHeading.compareDocumentPosition(timelineHeading),
      timelineHeading.compareDocumentPosition(artifactsHeading),
    ];
    expect(positions[0] & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(positions[1] & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await waitFor(() => {
      const urls = fetchSpy.mock.calls.map(([input]) => String(input));
      const detailIndex = urls.findIndex((url) => url.includes('/api/executions/test-123?source=temporal'));
      const stepsIndex = urls.findIndex((url) => url.includes('/api/executions/test-123/steps'));
      const artifactsIndex = urls.findIndex((url) => url.includes('/artifacts'));
      expect(detailIndex).toBeGreaterThanOrEqual(0);
      expect(stepsIndex).toBeGreaterThan(detailIndex);
      expect(artifactsIndex).toBeGreaterThan(stepsIndex);
    });
  });


  it('renders executing detail pills with the shared shimmer selector contract and keeps dependency pills non-executing when appropriate', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Executing detail task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
      dependents: [
        {
          workflowId: 'dep-1',
          title: 'Waiting dependent',
          state: 'waiting_on_dependencies',
          summary: 'Waiting summary',
          closeStatus: null,
        },
      ],
      prerequisites: [],
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await screen.findByText('Executing detail task');
    const toolbarStatus = document.querySelector<HTMLElement>('.toolbar-identity-row [data-effect="shimmer-sweep"]');
    expect(toolbarStatus?.dataset.state).toBe('executing');
    expect(toolbarStatus?.dataset.effect).toBe('shimmer-sweep');
    expect(toolbarStatus?.className).toContain('is-executing');
    expect(toolbarStatus?.className).toContain('status-running');
    expect(toolbarStatus?.childElementCount).toBe(0);
    expect(toolbarStatus?.textContent).toBe('executing');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-489');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-490');

    const waitingPill = await screen.findByText('waiting_on_dependencies');
    expect(waitingPill.closest('span')?.dataset.effect).toBeUndefined();
  });

  it('renders workload metadata and artifact refs on a workload-producing step', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Workload detail task',
      summary: 'Execution summary',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'completed',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };
    const workloadStepsSnapshot = {
      workflowId: 'test-123',
      runId: '02-run',
      runScope: 'latest',
      steps: [
        {
          logicalStepId: 'workload-step',
          order: 1,
          title: 'Run Unreal tests',
          tool: { type: 'skill', name: 'container.run_workload', version: '1' },
          dependsOn: [],
          status: 'failed',
          waitingReason: null,
          attentionRequired: false,
          attempt: 1,
          startedAt: '2026-04-09T00:00:01Z',
          updatedAt: '2026-04-09T00:00:04Z',
          summary: 'Workload failed',
          checks: [],
          refs: { childWorkflowId: null, childRunId: null, taskRunId: 'task-run-workload' },
          artifacts: {
            outputSummary: 'art-summary',
            outputPrimary: 'art-report',
            runtimeStdout: 'art-stdout',
            runtimeStderr: 'art-stderr',
            runtimeMergedLogs: null,
            runtimeDiagnostics: 'art-diagnostics',
            providerSnapshot: null,
          },
          workload: {
            taskRunId: 'task-run-workload',
            stepId: 'workload-step',
            attempt: 1,
            toolName: 'container.run_workload',
            profileId: 'unreal-5_3-linux',
            imageRef: 'registry.example/unreal-runner:5.3',
            status: 'failed',
            exitCode: 7,
            durationSeconds: 42.5,
            artifactPublication: {
              status: 'failed',
              error: 'diagnostics store unavailable',
            },
            sessionContext: {
              sessionId: 'session-1',
              sessionEpoch: 3,
              sourceTurnId: 'turn-9',
            },
          },
          lastError: null,
        },
      ],
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({
          ok: true,
          json: async () => workloadStepsSnapshot,
        } as Response);
      }
      if (url.includes('/task-runs/task-run-workload/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              runId: 'task-run-workload',
              status: 'failed',
              supportsLiveStreaming: false,
              liveStreamStatus: 'ended',
            },
          }),
        } as Response);
      }
      if (url.includes('/task-runs/task-run-workload/observability/events')) {
        return Promise.resolve({ ok: true, json: async () => ({ events: [], truncated: false }) } as Response);
      }
      if (url.includes('/task-runs/task-run-workload/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => 'workload stdout tail\n' } as unknown as Response);
      }
      if (url.includes('/task-runs/task-run-workload/logs/stdout')) {
        return Promise.resolve({ ok: true, text: async () => 'workload stdout\n' } as unknown as Response);
      }
      if (url.includes('/task-runs/task-run-workload/logs/stderr')) {
        return Promise.resolve({ ok: true, text: async () => 'workload stderr\n' } as unknown as Response);
      }
      if (url.includes('/task-runs/task-run-workload/diagnostics')) {
        return Promise.resolve({ ok: true, text: async () => '{"status":"failed"}\n' } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={stepsPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Run Unreal tests' }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workload' })).toBeTruthy();
      expect(screen.getByText('unreal-5_3-linux')).toBeTruthy();
      expect(screen.getByText('registry.example/unreal-runner:5.3')).toBeTruthy();
      expect(screen.getByText('art-stdout')).toBeTruthy();
      expect(screen.getByText('art-stderr')).toBeTruthy();
      expect(screen.getByText('art-diagnostics')).toBeTruthy();
      expect(screen.getByText('diagnostics store unavailable')).toBeTruthy();
      expect(screen.getByText(/session-1/)).toBeTruthy();
      expect(screen.queryByText(/managed session/i)).toBeNull();
    });
  });

  it('loads execution-wide artifacts against the latest run exposed by the step ledger', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Run rotation task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...latestStepsSnapshot,
            runId: '02-run',
          }),
        } as Response);
      }
      if (url.includes('/executions/default/test-123/02-run/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      if (url.includes('/executions/default/test-123/01-run/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Steps' })).toBeTruthy();
    });

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([url]) =>
          String(url).includes('/executions/default/test-123/02-run/artifacts'),
        ),
      ).toBe(true);
    });
    expect(
      fetchSpy.mock.calls.some(([url]) =>
        String(url).includes('/executions/default/test-123/01-run/artifacts'),
      ),
    ).toBe(false);
  });

  it('expands a bound step into grouped step details and lazily attaches row-scoped observability', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Step detail task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => latestStepsSnapshot } as Response);
      }
      if (url.includes('/task-runs/task-run-step-1/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: false,
              liveStreamStatus: 'unavailable',
            },
          }),
        } as Response);
      }
      if (url.includes('/task-runs/task-run-step-1/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-09T00:00:05Z',
                stream: 'stdout',
                text: 'step scoped log line\n',
              },
            ],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/task-runs/task-run-step-1/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => 'step scoped log line\n' } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Steps' })).toBeTruthy();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/task-runs/task-run-step-1/observability-summary')),
    ).toBe(false);

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: 'Summary' }).length).toBeGreaterThan(0);
      expect(screen.getByRole('heading', { name: 'Checks' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Logs & Diagnostics' })).toBeTruthy();
      expect(screen.getAllByRole('heading', { name: 'Artifacts' }).length).toBeGreaterThan(0);
      expect(screen.getByRole('heading', { name: 'Metadata' })).toBeTruthy();
      expect(screen.getByText('Auto-approved')).toBeTruthy();
      expect(screen.getByText('art-step-summary')).toBeTruthy();
      expect(screen.getByText('child-wf-1')).toBeTruthy();
      expect(screen.getByText('step scoped log line')).toBeTruthy();
      expect(screen.getByRole('button', { name: 'Hide details for Apply patch' })).toBeTruthy();
    });
    expect(screen.getAllByText('approval policy: passed')[0]?.className).toContain('check-passed');

    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/task-runs/task-run-step-1/observability-summary')),
    ).toBe(true);
  });

  it('keeps unbound rows free of task-run requests and upgrades expanded rows when taskRunId arrives later', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Delayed binding task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };
    let stepCalls = 0;

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        stepCalls += 1;
        return Promise.resolve({
          ok: true,
          json: async () =>
            stepCalls === 1
              ? {
                  ...latestStepsSnapshot,
                  steps: latestStepsSnapshot.steps.map((step) =>
                    step.logicalStepId === 'apply'
                      ? {
                          ...step,
                          refs: {
                            ...step.refs,
                            taskRunId: null,
                          },
                        }
                      : step,
                  ),
                }
              : latestStepsSnapshot,
        } as Response);
      }
      if (url.includes('/task-runs/task-run-step-1/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: false,
              liveStreamStatus: 'unavailable',
            },
          }),
        } as Response);
      }
      if (url.includes('/task-runs/task-run-step-1/observability/events')) {
        return Promise.resolve({ ok: false, status: 404 } as Response);
      }
      if (url.includes('/task-runs/task-run-step-1/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => 'attached after refresh\n' } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Steps' })).toBeTruthy();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/task-runs/task-run-step-1/observability-summary')),
    ).toBe(false);

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    await waitFor(() => {
      expect(screen.getByText('attached after refresh')).toBeTruthy();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/task-runs/task-run-step-1/observability-summary')),
    ).toBe(true);
  });

  it('renders retry counts and review artifact refs inside the Checks section', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Review detail task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...latestStepsSnapshot,
            steps: latestStepsSnapshot.steps.map((step) =>
              step.logicalStepId === 'apply'
                ? {
                    ...step,
                    status: 'reviewing',
                    checks: [
                      {
                        kind: 'approval_policy',
                        status: 'failed',
                        summary: 'Reviewer requested another retry',
                        retryCount: 2,
                        artifactRef: 'art-review-2',
                      },
                    ],
                  }
                : step,
            ),
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Steps' })).toBeTruthy();
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    await waitFor(() => {
      expect(screen.getByText('Retry count: 2')).toBeTruthy();
      expect(screen.getByText('art-review-2')).toBeTruthy();
      expect(screen.getAllByText('approval policy: failed')[0]).toBeTruthy();
    });
  });

  it('resolves step-level task-run routes against apiBase', async () => {
    const apiBasePayload: BootPayload = {
      ...stepsPayload,
      apiBase: '/tenant/api',
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/tenant/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Task behind apiBase',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/tenant/api/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => latestStepsSnapshot } as Response);
      }
      if (url.includes('/tenant/api/task-runs/task-run-step-1/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: false,
              liveStreamStatus: 'unavailable',
            },
          }),
        } as Response);
      }
      if (url.includes('/tenant/api/task-runs/task-run-step-1/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ events: [], truncated: false }),
        } as Response);
      }
      if (url.includes('/tenant/api/task-runs/task-run-step-1/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => '' } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={apiBasePayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Steps' })).toBeTruthy();
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([url]) =>
          String(url).includes('/tenant/api/task-runs/task-run-step-1/observability-summary'),
        ),
      ).toBe(true);
    });
  });

  it('shows the execution Observation fallback when the steps endpoint fails', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      taskRunId: 'task-run-root',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Fallback observation task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: false, status: 403, statusText: '' } as Response);
      }
      if (url.includes('/task-runs/task-run-root/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: false,
              liveStreamStatus: 'unavailable',
            },
          }),
        } as Response);
      }
      if (url.includes('/task-runs/task-run-root/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-09T00:00:05Z',
                stream: 'stdout',
                text: 'root observation log\n',
              },
            ],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/task-runs/task-run-root/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => 'root observation log\n' } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Steps: 403 (/api/executions/test-123/steps)')).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Observation' })).toBeTruthy();
    });

    fireEvent.click(screen.getByText('Live Logs'));

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([url]) => String(url).includes('/task-runs/task-run-root/observability-summary')),
      ).toBe(true);
    });
  });

  it('does not attach step-level observability when log streaming is disabled', async () => {
    const logStreamingDisabledPayload: BootPayload = {
      ...stepsPayload,
      initialData: {
        dashboardConfig: {
          ...((stepsPayload.initialData as { dashboardConfig: unknown }).dashboardConfig as Record<string, unknown>),
          features: {
            logStreamingEnabled: false,
          },
        },
      },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Streaming disabled task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => latestStepsSnapshot } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={logStreamingDisabledPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Steps' })).toBeTruthy();
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    await waitFor(() => {
      expect(screen.getByText(/live log streaming is disabled in the server dashboard config/i)).toBeTruthy();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/task-runs/task-run-step-1/observability-summary')),
    ).toBe(false);
  });

  it('renders loading state initially', () => {
    fetchSpy.mockImplementation(() => new Promise(() => {}));
    renderWithClient(<TaskDetailPage payload={mockPayload} />);
    expect(screen.getByText(/Loading task/i)).toBeTruthy();
  });

  it('builds canonical Temporal task editing routes', () => {
    expect(taskEditHref('mm:wf 1')).toBe('/tasks/new?editExecutionId=mm%3Awf%201');
    expect(taskRerunHref('mm:wf 1')).toBe('/tasks/new?rerunExecutionId=mm%3Awf%201');
  });

  it('shows Edit and Rerun entry points only when Temporal task editing is flagged on and capabilities allow them', async () => {
    vi.spyOn(console, 'info').mockImplementation(() => {});
    const telemetryEvents: Array<Record<string, unknown>> = [];
    const onTelemetry = (event: Event) => {
      telemetryEvents.push((event as CustomEvent).detail);
    };
    window.addEventListener('moonmind:temporal-task-editing', onTelemetry);
    const actionPayload: BootPayload = {
      ...mockPayload,
      initialData: {
        dashboardConfig: {
          features: {
            temporalDashboard: {
              actionsEnabled: true,
              temporalTaskEditing: true,
            },
          },
        },
      },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Editable task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canSetTitle: true,
        canUpdateInputs: true,
        canRerun: true,
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Task Actions' })).toBeTruthy();
    });
    expect(screen.getByRole('link', { name: 'Edit' }).getAttribute('href')).toBe(
      '/tasks/new?editExecutionId=test-123',
    );
    expect(screen.getByRole('link', { name: 'Rerun' }).getAttribute('href')).toBe(
      '/tasks/new?rerunExecutionId=test-123',
    );
    const editLink = screen.getByRole('link', { name: 'Edit' });
    const rerunLink = screen.getByRole('link', { name: 'Rerun' });
    editLink.addEventListener('click', (event) => event.preventDefault());
    rerunLink.addEventListener('click', (event) => event.preventDefault());
    fireEvent.click(editLink);
    fireEvent.click(rerunLink);
    expect(telemetryEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: 'detail_edit_click',
          mode: 'detail',
          workflowId: 'test-123',
        }),
        expect.objectContaining({
          event: 'detail_rerun_click',
          mode: 'detail',
          workflowId: 'test-123',
        }),
      ]),
    );
    window.removeEventListener('moonmind:temporal-task-editing', onTelemetry);
  });

  it('shows a one-time Temporal task editing success notice after redirect', async () => {
    window.sessionStorage.setItem(
      'moonmind.temporalTaskEditing.notice',
      'Changes were saved to this execution.',
    );
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Edited task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    expect(await screen.findByText('Changes were saved to this execution.')).toBeTruthy();
    expect(screen.getByRole('status')).toBeTruthy();
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123?source=temporal',
      );
    });
    expect(
      window.sessionStorage.getItem('moonmind.temporalTaskEditing.notice'),
    ).toBeNull();
  });

  it('prevents task editing navigation while another action is pending', async () => {
    const actionPayload: BootPayload = {
      ...mockPayload,
      initialData: {
        dashboardConfig: {
          features: {
            temporalDashboard: {
              actionsEnabled: true,
              temporalTaskEditing: true,
            },
          },
        },
      },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Editable task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canSetTitle: true,
        canUpdateInputs: true,
        canRerun: true,
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/update')) {
        return new Promise<Response>(() => {});
      }
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('Renamed task');

    renderWithClient(<TaskDetailPage payload={actionPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Rerun' })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));

    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Rerun' }).getAttribute('aria-disabled')).toBe('true');
    });

    const clickEvent = new MouseEvent('click', { bubbles: true, cancelable: true });
    const allowed = screen.getByRole('link', { name: 'Rerun' }).dispatchEvent(clickEvent);

    expect(allowed).toBe(false);
    expect(clickEvent.defaultPrevented).toBe(true);
    promptSpy.mockRestore();
  });

  it('omits Temporal task editing entry points when the flag is off', async () => {
    const actionPayload: BootPayload = {
      ...mockPayload,
      initialData: {
        dashboardConfig: {
          features: {
            temporalDashboard: {
              actionsEnabled: true,
              temporalTaskEditing: false,
            },
          },
        },
      },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      title: 'Flagged off task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canUpdateInputs: true,
        canRerun: true,
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Flagged off task')).toBeTruthy();
    });
    expect(screen.queryByRole('link', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Rerun' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Task Actions' })).toBeNull();
  });

  it('renders task details on successful fetch', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      targetRuntime: 'gemini_cli',
      targetSkill: 'jira-pr-verify',
      taskSkills: ['jira-pr-verify', 'fix-comments'],
      skillRuntime: {
        resolvedSkillsetRef: 'artifact:resolved-skills-1',
        selectedSkills: ['jira-pr-verify'],
        selectedVersions: [
          {
            name: 'jira-pr-verify',
            version: '1.2.0',
            sourceKind: 'deployment',
            contentRef: 'artifact:skill-body-1',
            contentDigest: 'sha256:abc',
          },
        ],
        sourceProvenance: [{ name: 'jira-pr-verify', sourceKind: 'deployment' }],
        materializationMode: 'hybrid',
        visiblePath: '.agents/skills',
        backingPath: '../skills_active',
        readOnly: true,
        manifestRef: 'artifact:manifest-1',
        promptIndexRef: 'artifact:prompt-index-1',
        activationSummaryRef: 'artifact:activation-summary-1',
        lifecycleIntent: {
          source: 'run',
          resolutionMode: 'snapshot-reuse',
          explanation:
            'Execution reuses the resolved skill snapshot unless explicit re-resolution is requested.',
        },
      },
      profileId: 'profile:gemini-default',
      providerId: 'google',
      providerLabel: 'Google',
      title: 'Example task',
      summary: 'Did work',
      taskInstructions: 'Inspect the repository.\n\nThen run the focused UI tests.',
      status: 'completed',
      state: 'succeeded',
      prUrl: 'https://github.com/MoonLadderStudios/MoonMind/pull/123',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      closeStatus: 'COMPLETED',
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: '2026-03-28T00:00:03Z',
      actions: { canSetTitle: true, canCancel: false, canRerun: false },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-complete/remediations?direction=')) {
        return Promise.resolve({ ok: true, json: async () => ({ direction: 'inbound', items: [] }) } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Example task')).toBeTruthy();
      expect(screen.getByText('Did work')).toBeTruthy();
      expect(screen.getByText('Gemini CLI')).toBeTruthy();
      expect(screen.getByText('Explicit Selection').closest('div')?.textContent).toContain(
        'jira-pr-verify, fix-comments',
      );
      expect(screen.getByText('Delegated Skill').closest('div')?.textContent).toContain('jira-pr-verify');
      expect(screen.getByText('Selected Versions').closest('div')?.textContent).toContain(
        'jira-pr-verify@1.2.0',
      );
      expect(screen.getByText('Source Provenance').closest('div')?.textContent).toContain(
        'deployment',
      );
      expect(screen.getByText('Materialization').closest('div')?.textContent).toContain('hybrid');
      expect(screen.getByText('Visible Path').closest('div')?.textContent).toContain('.agents/skills');
      expect(screen.getByText('Backing Path').closest('div')?.textContent).toContain('../skills_active');
      expect(screen.getByText('Manifest Ref').closest('div')?.textContent).toContain('artifact:manifest-1');
      expect(screen.getByText('Prompt Index Ref').closest('div')?.textContent).toContain(
        'artifact:prompt-index-1',
      );
      expect(screen.getByText('Lifecycle Intent').closest('div')?.textContent).toContain('snapshot-reuse');
      expect(screen.queryByText('FULL SKILL BODY SHOULD NOT LEAK')).toBeNull();
      expect(screen.getByText('Google')).toBeTruthy();
      expect(screen.getByText('profile:gemini-default')).toBeTruthy();
      expect(screen.getByRole('link', { name: 'https://github.com/MoonLadderStudios/MoonMind/pull/123' })).toBeTruthy();
    });

    expect(screen.queryByText('Inspect the repository.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Show instructions/ }));
    expect(screen.getByRole('button', { name: /Hide instructions/ }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(/Inspect the repository\./)).toBeTruthy();
    expect(screen.getByText(/Then run the focused UI tests\./)).toBeTruthy();

    expect(fetchSpy).toHaveBeenCalledWith('/api/executions/test-123?source=temporal');
  });

  it('renders remediation create action, relationships, evidence, and degraded states', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Failed target task',
      summary: 'Needs remediation.',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      repository: 'MoonLadderStudios/MoonMind',
      createdAt: '2026-04-22T00:00:00Z',
      updatedAt: '2026-04-22T00:00:01Z',
      actions: { canSetTitle: true },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/executions/test-123/remediations?direction=inbound')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            direction: 'inbound',
            items: [
              {
                remediationWorkflowId: 'mm:remediation-1',
                remediationRunId: 'run-remediation-1',
                targetWorkflowId: 'test-123',
                targetRunId: '01-run',
                mode: 'snapshot_then_follow',
                authorityMode: 'approval_gated',
                status: 'awaiting_approval',
                activeLockScope: 'target_execution',
                activeLockHolder: 'mm:remediation-1',
                latestActionSummary: 'Proposed session interrupt',
                resolution: null,
                contextArtifactRef: 'art_context',
                approvalState: { requestId: 'approval-1', decision: 'pending', canDecide: true },
                createdAt: '2026-04-22T00:00:02Z',
                updatedAt: '2026-04-22T00:00:03Z',
              },
            ],
          }),
        } as Response);
      }
      if (url.includes('/executions/test-123/remediations?direction=outbound')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            direction: 'outbound',
            items: [
              {
                remediationWorkflowId: 'test-123',
                remediationRunId: '01-run',
                targetWorkflowId: 'mm:target-1',
                targetRunId: 'run-target',
                mode: 'snapshot',
                authorityMode: 'observe_only',
                status: 'created',
                contextArtifactRef: null,
                approvalState: null,
              },
            ],
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/executions/default/test-123/01-run/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifactId: 'art_context',
                contentType: 'application/json',
                sizeBytes: 128,
                status: 'complete',
                metadata: { artifact_type: 'remediation.context' },
                links: [],
              },
            ],
          }),
        } as Response);
      }
      if (url.includes('/executions/test-123/remediation') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ workflowId: 'mm:remediation-created' }),
        } as Response);
      }
      if (url.includes('/executions/mm%3Aremediation-1/remediation/approvals/approval-1') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            accepted: true,
            workflowId: 'mm:remediation-1',
            requestId: 'approval-1',
            decision: 'approved',
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionsPayload} />);

    expect(await screen.findByRole('button', { name: 'Create remediation task' })).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Remediation' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Remediation Tasks' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Remediation Target' })).toBeTruthy();
    expect(screen.getByText('mm:remediation-1')).toBeTruthy();
    expect(screen.getByText('mm:target-1')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Remediation Evidence' })).toBeTruthy();
    expect(screen.getByText('Context')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Evidence' }).getAttribute('href')).toBe(
      '/api/artifacts/art_context/download',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Create remediation task' }));

    await waitFor(() => {
      const remediationCreateCall = fetchSpy.mock.calls.find(
        ([url, init]) => String(url) === '/api/executions/test-123/remediation' && init?.method === 'POST',
      );
      expect(remediationCreateCall).toBeTruthy();
      expect(JSON.parse(String(remediationCreateCall?.[1]?.body))).toMatchObject({
        repository: 'MoonLadderStudios/MoonMind',
        remediation: {
          mode: 'snapshot_then_follow',
          authorityMode: 'approval_gated',
          target: { runId: '01-run' },
          evidencePolicy: {
            includeStepLedger: true,
            includeDiagnostics: true,
            tailLines: 2000,
          },
          trigger: { type: 'manual' },
        },
      });
    });

    fireEvent.click(screen.getByRole('button', { name: 'Approve remediation action' }));

    await waitFor(() => {
      const approvalCall = fetchSpy.mock.calls.find(
        ([url, init]) =>
          String(url) === '/api/executions/mm%3Aremediation-1/remediation/approvals/approval-1' &&
          init?.method === 'POST',
      );
      expect(approvalCall).toBeTruthy();
      expect(JSON.parse(String(approvalCall?.[1]?.body))).toEqual({
        decision: 'approved',
      });
    });
  });

  it('lets operators choose remediation mode, authority, and action policy before submission', async () => {
    const mockExecution = {
      taskId: 'test-remediation-create-choices',
      workflowId: 'test-remediation-create-choices',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Failed target with choices',
      summary: 'Needs remediation.',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      repository: 'MoonLadderStudios/MoonMind',
      createdAt: '2026-04-22T00:00:00Z',
      updatedAt: '2026-04-22T00:00:01Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/executions/test-remediation-create-choices/remediations?direction=')) {
        return Promise.resolve({ ok: true, json: async () => ({ direction: 'inbound', items: [] }) } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/executions/test-remediation-create-choices/remediation') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ workflowId: 'mm:remediation-created' }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionsPayload} />);

    expect(await screen.findByText('Remediation create preview')).toBeTruthy();
    expect(screen.getByText(/Evidence preview: step ledger, diagnostics, and 2000 log lines/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Remediation mode'), {
      target: { value: 'snapshot' },
    });
    fireEvent.change(screen.getByLabelText('Remediation authority'), {
      target: { value: 'observe_only' },
    });
    fireEvent.change(screen.getByLabelText('Remediation action policy'), {
      target: { value: 'troubleshooting_only' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create remediation task' }));

    await waitFor(() => {
      const remediationCreateCall = fetchSpy.mock.calls.find(
        ([url, init]) =>
          String(url) === '/api/executions/test-remediation-create-choices/remediation' &&
          init?.method === 'POST',
      );
      expect(remediationCreateCall).toBeTruthy();
      expect(JSON.parse(String(remediationCreateCall?.[1]?.body))).toMatchObject({
        remediation: {
          mode: 'snapshot',
          authorityMode: 'observe_only',
          actionPolicyRef: 'troubleshooting_only',
          target: { runId: '01-run' },
          evidencePolicy: {
            includeStepLedger: true,
            includeDiagnostics: true,
            tailLines: 2000,
          },
        },
      });
    });
  });

  it('hides remediation creation for ineligible completed targets', async () => {
    const mockExecution = {
      taskId: 'test-complete',
      workflowId: 'test-complete',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Completed target task',
      summary: 'No follow-up needed.',
      status: 'completed',
      state: 'succeeded',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      createdAt: '2026-04-22T00:00:00Z',
      updatedAt: '2026-04-22T00:00:01Z',
      actions: { canSetTitle: true },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionsPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Completed target task')).toBeTruthy();
    });

    expect(screen.queryByRole('button', { name: 'Create remediation task' })).toBeNull();
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/remediations?direction=inbound'))).toBe(true);
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/remediations?direction=outbound'))).toBe(true);
  });

  it('renders approval-gated remediation as read-only when the operator cannot decide', async () => {
    const mockExecution = {
      taskId: 'test-readonly-approval',
      workflowId: 'test-readonly-approval',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Remediation target task',
      summary: 'Needs remediation.',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      createdAt: '2026-04-22T00:00:00Z',
      updatedAt: '2026-04-22T00:00:01Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-readonly-approval/remediations?direction=inbound')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            direction: 'inbound',
            items: [
              {
                remediationWorkflowId: 'mm:remediation-readonly',
                remediationRunId: 'run-remediation-readonly',
                targetWorkflowId: 'test-readonly-approval',
                targetRunId: '01-run',
                mode: 'snapshot_then_follow',
                authorityMode: 'approval_gated',
                status: 'awaiting_approval',
                activeLockScope: 'target_execution',
                latestActionSummary: 'Proposed session interrupt',
                approvalState: {
                  requestId: 'approval-readonly',
                  actionKind: 'session_interrupt',
                  riskTier: 'high',
                  preconditions: 'Target run is still active.',
                  blastRadius: 'One managed session.',
                  decision: 'pending',
                  canDecide: false,
                },
                createdAt: '2026-04-22T00:00:02Z',
                updatedAt: '2026-04-22T00:00:03Z',
              },
            ],
          }),
        } as Response);
      }
      if (url.includes('/executions/test-readonly-approval/remediations?direction=outbound')) {
        return Promise.resolve({ ok: true, json: async () => ({ direction: 'outbound', items: [] }) } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionsPayload} />);

    expect(await screen.findByText('mm:remediation-readonly')).toBeTruthy();
    expect(screen.getByText('session_interrupt')).toBeTruthy();
    expect(screen.getByText('high')).toBeTruthy();
    expect(screen.getByText('Target run is still active.')).toBeTruthy();
    expect(screen.getByText('One managed session.')).toBeTruthy();
    expect(screen.getByText('Approval is read-only for this operator.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Approve remediation action' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reject remediation action' })).toBeNull();
  });

  it('renders degraded remediation states for missing links, evidence, and live follow data', async () => {
    const mockExecution = {
      taskId: 'test-remediation-degraded',
      workflowId: 'test-remediation-degraded',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Remediation task',
      summary: 'Remediation work with partial evidence.',
      status: 'running',
      state: 'running',
      rawState: 'running',
      temporalStatus: 'running',
      createdAt: '2026-04-22T00:00:00Z',
      updatedAt: '2026-04-22T00:00:01Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-remediation-degraded/remediations?direction=inbound')) {
        return Promise.resolve({ ok: true, json: async () => ({ direction: 'inbound', items: [] }) } as Response);
      }
      if (url.includes('/executions/test-remediation-degraded/remediations?direction=outbound')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            direction: 'outbound',
            items: [
              {
                remediationWorkflowId: 'test-remediation-degraded',
                remediationRunId: '01-run',
                targetWorkflowId: 'mm:target-long-workflow-id-with-many-segments-for-mobile-containment',
                targetRunId: 'run-target-with-a-very-long-identifier-for-mobile-containment',
                mode: 'snapshot_then_follow',
                authorityMode: 'approval_gated',
                status: 'collecting_context',
                contextArtifactRef: null,
                approvalState: { requestId: 'approval-missing', decision: 'pending', canDecide: false },
              },
            ],
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Remediation' })).toBeTruthy();
    expect(screen.getByText('No inbound remediation tasks linked yet.')).toBeTruthy();
    expect(screen.getByText('Evidence bundle is missing.')).toBeTruthy();
    expect(screen.getByText('Live follow is unavailable; durable remediation artifacts remain authoritative.')).toBeTruthy();
    expect(screen.getByText('No remediation evidence artifacts linked yet.')).toBeTruthy();

    const longTarget = screen.getByText('mm:target-long-workflow-id-with-many-segments-for-mobile-containment');
    expect(longTarget.closest('code')?.className).toContain('break-all');
  });

  it('keeps remediation panels accessible and contained in Mission Control CSS', async () => {
    const { readFileSync } = await import('node:fs');
    const missionControlCss = readFileSync(
      `${process.cwd()}/frontend/src/styles/mission-control.css`,
      'utf8',
    );

    expect(missionControlCss).toMatch(/\.td-remediation-region:focus-within\s*\{[^}]*outline:\s*2px solid/s);
    expect(missionControlCss).toMatch(/\.td-remediation-list\s+\.card\s*\{[^}]*min-width:\s*0;[^}]*max-width:\s*100%;/s);
    expect(missionControlCss).toMatch(/@media\s*\(max-width:\s*720px\)\s*\{[^}]*\.td-remediation-region/s);
    expect(missionControlCss).toMatch(/\.td-remediation-list\s+code\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
  });

  it('renders task detail as separated matte evidence and action regions', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Example task',
      summary: 'Did work',
      taskInstructions: 'Inspect the repository.',
      status: 'completed',
      state: 'succeeded',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      closeStatus: 'COMPLETED',
      stepsHref: '/api/executions/test-123/steps',
      taskRunId: 'task-run-1',
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: '2026-03-28T00:00:03Z',
      actions: { canSetTitle: true, canCancel: false, canRerun: false },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/steps')) {
        return Promise.resolve({
          ok: true,
          json: async () => latestStepsSnapshot,
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifactId: 'artifact-output',
                contentType: 'text/plain',
                sizeBytes: 42,
                status: 'complete',
                downloadUrl: '/api/artifacts/artifact-output/download',
              },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    const mm428CompositionPayload: BootPayload = {
      ...stepsPayload,
      initialData: {
        dashboardConfig: {
          ...(stepsPayload.initialData as { dashboardConfig: Record<string, unknown> }).dashboardConfig,
          features: { temporalDashboard: { actionsEnabled: true } },
        },
      },
    };

    renderWithClient(<TaskDetailPage payload={mm428CompositionPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Example task')).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Steps' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Artifacts' })).toBeTruthy();
      expect(screen.getByText('artifact-output')).toBeTruthy();
    });

    const root = document.querySelector<HTMLElement>('.task-detail-page');
    const summary = document.querySelector<HTMLElement>('.td-summary-block');
    const facts = document.querySelector<HTMLElement>('.td-facts-region');
    const steps = document.querySelector<HTMLElement>('.td-steps-region.td-evidence-region');
    const timeline = document.querySelector<HTMLElement>('.td-timeline-region.td-evidence-region');
    const artifacts = document.querySelector<HTMLElement>('.td-artifacts-region.td-evidence-region');
    const actions = document.querySelector<HTMLElement>('.td-actions-region');

    expect(root).not.toBeNull();
    expect(summary).not.toBeNull();
    expect(facts).not.toBeNull();
    expect(steps).not.toBeNull();
    expect(timeline).not.toBeNull();
    expect(artifacts).not.toBeNull();
    expect(actions).not.toBeNull();
    expect(artifacts?.querySelector('.td-evidence-slab.queue-table-wrapper')).not.toBeNull();
    expect(timeline?.querySelector('.td-evidence-slab.queue-table-wrapper')).not.toBeNull();
    expect(root?.querySelector('.td-evidence-region .panel--floating')).toBeNull();
    expect(root?.querySelector('.td-evidence-region .queue-floating-bar')).toBeNull();
  });

  it('renders empty skill provenance when task skill metadata is missing', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Example task',
      summary: 'Did work',
      status: 'completed',
      state: 'succeeded',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      closeStatus: 'COMPLETED',
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: '2026-03-28T00:00:03Z',
      actions: { canSetTitle: true, canCancel: false, canRerun: false },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Example task')).toBeTruthy();
    });

    expect(screen.getByText('Explicit Selection').closest('div')?.textContent).toContain('None');
    expect(screen.getByText('Delegated Skill').closest('div')?.textContent).toContain('—');
  });

  it('renders structured run summary details from the summary artifact', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Explained task',
      summary: "publishMode 'pr' requested but no local changes were produced",
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      closeStatus: 'FAILED',
      summaryArtifactRef: 'art-summary-1',
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: '2026-03-28T00:00:03Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts/art-summary-1/download')) {
        return Promise.resolve({
          ok: true,
          text: async () =>
            JSON.stringify({
              finishOutcome: {
                code: 'FAILED',
                stage: 'finalizing',
                reason: "publishMode 'pr' requested, but no publishable diff was produced.",
              },
              publish: {
                mode: 'pr',
                status: 'failed',
                reason:
                  "publishMode 'pr' requested, but no publishable diff was produced. branch 'feature/no-op' has no commits ahead of origin/main.",
              },
              operatorSummary:
                'The requested behavior already existed in the repo.\nFiles edited in this run: none.',
              publishContext: {
                branch: 'feature/no-op',
                baseRef: 'origin/main',
                commitCount: 0,
                pullRequestUrl: 'https://github.com/MoonLadderStudios/MoonMind/pull/456',
              },
              lastStep: {
                summary: 'Files edited in this run: none',
              },
            }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Explained task')).toBeTruthy();
      expect(screen.getByText(/The requested behavior already existed in the repo\./)).toBeTruthy();
      expect(screen.getByText('Run Summary')).toBeTruthy();
      expect(screen.getByText('feature/no-op')).toBeTruthy();
      expect(screen.getByText('origin/main')).toBeTruthy();
      expect(screen.getByRole('link', { name: 'https://github.com/MoonLadderStudios/MoonMind/pull/456' })).toBeTruthy();
      expect(screen.getAllByText(/no publishable diff was produced/).length).toBeGreaterThan(0);
    });
  });

  it('does not render a PR link for unsafe execution or run-summary URLs', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Unsafe task',
      summary: 'Ignore unsafe PR links',
      status: 'completed',
      state: 'succeeded',
      prUrl: 'javascript:alert(1)',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      closeStatus: 'COMPLETED',
      summaryArtifactRef: 'art-summary-unsafe',
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: '2026-03-28T00:00:03Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts/art-summary-unsafe/download')) {
        return Promise.resolve({
          ok: true,
          text: async () =>
            JSON.stringify({
              publishContext: {
                pullRequestUrl: 'javascript:alert(2)',
              },
            }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Unsafe task')).toBeTruthy();
    });

    expect(screen.queryByText('PR Link')).toBeNull();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('renders merge automation visibility from the run summary', async () => {
    const mockExecution = {
      taskId: 'test-merge-visibility',
      workflowId: 'test-merge-visibility',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Merge visibility task',
      summary: 'Waiting on merge automation',
      status: 'running',
      state: 'awaiting_external',
      rawState: 'awaiting_external',
      temporalStatus: 'running',
      closeStatus: null,
      summaryArtifactRef: 'art-summary-merge',
      mergeAutomationSelected: true,
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: null,
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts/art-summary-merge/download')) {
        return Promise.resolve({
          ok: true,
          text: async () =>
            JSON.stringify({
              mergeAutomation: {
                enabled: true,
                status: 'blocked',
                prNumber: 354,
                prUrl: 'https://github.com/MoonLadderStudios/MoonMind/pull/354',
                latestHeadSha: 'abc123',
                cycles: 2,
                childWorkflowId: 'merge-automation:wf-parent',
                resolverChildWorkflowIds: ['resolver:wf-parent:1', 'resolver:wf-parent:2'],
                blockers: [{ kind: 'checks_failed', summary: 'Required checks failed.' }],
                artifactRefs: {
                  summary: 'summary-artifact',
                  gateSnapshots: ['gate-snapshot-artifact'],
                  resolverAttempts: ['resolver-attempt-artifact'],
                },
              },
            }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getAllByText('Merge Automation').length).toBeGreaterThan(0);
      expect(
        screen
          .getAllByText('Merge Automation')
          .some((node) => node.closest('div')?.textContent?.includes('Selected')),
      ).toBe(true);
      expect(screen.getByText('blocked')).toBeTruthy();
      expect(screen.getByRole('link', { name: 'https://github.com/MoonLadderStudios/MoonMind/pull/354' })).toBeTruthy();
      expect(screen.getByText('abc123')).toBeTruthy();
      expect(screen.getByText('merge-automation:wf-parent')).toBeTruthy();
      expect(screen.getByText('resolver:wf-parent:1')).toBeTruthy();
      expect(screen.getByText('Required checks failed.')).toBeTruthy();
      expect(screen.getByText('summary-artifact')).toBeTruthy();
      expect(screen.queryByText('Schedule')).toBeNull();
    });
  });

  it('renders live merge automation visibility from execution detail', async () => {
    const mockExecution = {
      taskId: 'test-live-merge-visibility',
      workflowId: 'test-live-merge-visibility',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Live merge visibility task',
      summary: 'Waiting on merge automation',
      status: 'running',
      state: 'awaiting_external',
      rawState: 'awaiting_external',
      temporalStatus: 'running',
      closeStatus: null,
      mergeAutomationSelected: true,
      mergeAutomation: {
        enabled: true,
        workflowId: 'merge-automation:test-live-merge-visibility',
        status: 'waiting',
        prNumber: 1614,
        prUrl: 'https://github.com/MoonLadderStudios/MoonMind/pull/1614',
        latestHeadSha: 'abc123',
        blockers: [{ kind: 'checks_failed', summary: 'Required checks are failing.', source: 'github' }],
        resolverChildWorkflowIds: [],
        artifactRefs: {
          gateSnapshots: ['gate-snapshot-artifact'],
          resolverAttempts: [],
        },
      },
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: null,
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Live merge visibility task')).toBeTruthy();
      expect(screen.getByText('waiting')).toBeTruthy();
      expect(screen.getByText('merge-automation:test-live-merge-visibility')).toBeTruthy();
      expect(screen.getByText('Required checks are failing.')).toBeTruthy();
      expect(screen.getByText('Waiting for required checks before launching pr-resolver.')).toBeTruthy();
      expect(screen.getByText('gate-snapshot-artifact')).toBeTruthy();
    });
  });

  it('accepts null merge automation artifact refs from execution detail', async () => {
    const mockExecution = {
      taskId: 'test-null-merge-artifact-refs',
      workflowId: 'test-null-merge-artifact-refs',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Null merge artifact refs task',
      summary: 'Waiting on merge automation',
      status: 'running',
      state: 'awaiting_external',
      rawState: 'awaiting_external',
      temporalStatus: 'running',
      closeStatus: null,
      mergeAutomationSelected: true,
      mergeAutomation: {
        enabled: true,
        workflowId: 'merge-automation:test-null-merge-artifact-refs',
        status: 'waiting',
        resolverChildWorkflowIds: [],
        artifactRefs: null,
      },
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: null,
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Null merge artifact refs task')).toBeTruthy();
      expect(screen.getByText('waiting')).toBeTruthy();
      expect(screen.getByText('merge-automation:test-null-merge-artifact-refs')).toBeTruthy();
    });
  });

  it('renders prerequisite and dependent panels for dependency-aware runs', async () => {
    const mockExecution = {
      taskId: 'mm:dependent-1',
      workflowId: 'mm:dependent-1',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Dependent task',
      summary: 'Waiting on upstream work',
      status: 'waiting',
      state: 'waiting_on_dependencies',
      rawState: 'waiting_on_dependencies',
      temporalStatus: 'running',
      dependsOn: ['mm:dep-1'],
      hasDependencies: true,
      blockedOnDependencies: true,
      dependencyResolution: 'not_applicable',
      dependencyWaitDurationMs: 3200,
      prerequisites: [
        {
          workflowId: 'mm:dep-1',
          title: 'Build shared schema',
          summary: 'Finishing migrations',
          state: 'executing',
          closeStatus: null,
          workflowType: 'MoonMind.Run',
        },
      ],
      dependents: [
        {
          workflowId: 'mm:child-1',
          title: 'Run UI smoke tests',
          summary: 'Blocked on this task',
          state: 'waiting_on_dependencies',
          closeStatus: null,
          workflowType: 'MoonMind.Run',
        },
      ],
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Dependencies' })).toBeTruthy();
      expect(screen.getByText(/Blocked on prerequisites/i)).toBeTruthy();
      expect(screen.getByText('Build shared schema')).toBeTruthy();
      expect(screen.getByText('Run UI smoke tests')).toBeTruthy();
    });
  });

  it('signals a manual dependency wait bypass from the dependency panel', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const signalBodies: unknown[] = [];
    const mockExecution = {
      taskId: 'mm:dependent-1',
      workflowId: 'mm:dependent-1',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.Run',
      entry: 'run',
      title: 'Dependent task',
      summary: 'Waiting on upstream work',
      status: 'waiting',
      state: 'waiting_on_dependencies',
      rawState: 'waiting_on_dependencies',
      temporalStatus: 'running',
      dependsOn: ['mm:dep-1'],
      hasDependencies: true,
      blockedOnDependencies: true,
      dependencyResolution: 'not_applicable',
      prerequisites: [
        {
          workflowId: 'mm:dep-1',
          title: 'Build shared schema',
          summary: 'Finishing migrations',
          state: 'executing',
          closeStatus: null,
          workflowType: 'MoonMind.Run',
        },
      ],
      dependents: [],
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: { canBypassDependencies: true },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      if (url.endsWith('/api/executions/mm%3Adependent-1/signal')) {
        signalBodies.push(JSON.parse(String(init?.body || '{}')));
        return Promise.resolve({
          ok: true,
          json: async () => ({ ...mockExecution, blockedOnDependencies: false }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    window.history.pushState({}, 'Test', '/tasks/mm%3Adependent-1?source=temporal');
    renderWithClient(<TaskDetailPage payload={actionsPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Bypass Dependency Wait' }));

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledWith('Bypass dependency waiting for this task?');
      expect(signalBodies).toEqual([
        {
          signalName: 'BypassDependencies',
          payload: { reason: 'Dependency wait bypassed by operator from Mission Control.' },
        },
      ]);
    });
  });

  it('renders artifact rows from snake_case temporal artifact payloads', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Artifact task',
      summary: 'Artifact payload',
      status: 'running',
      state: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:00Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifact_id: 'art-001',
                content_type: 'application/json',
                size_bytes: 512,
                status: 'complete',
              },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Artifact task')).toBeTruthy();
      expect(screen.getByText('art-001')).toBeTruthy();
      expect(screen.getByText('512')).toBeTruthy();
      expect(screen.getByText('complete')).toBeTruthy();
      expect(screen.getByRole('link', { name: /Download/i }).getAttribute('href')).toBe(
        '/api/artifacts/art-001/download'
      );
    });
  });

  it('renders server-selected primary report before generic artifacts and keeps related report content openable', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Report task',
      summary: 'Report payload',
      status: 'completed',
      state: 'succeeded',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifact_id: 'art-report-primary',
                content_type: 'text/markdown',
                size_bytes: 2048,
                status: 'complete',
                metadata: {
                  title: 'Final implementation report',
                  report_type: 'implementation',
                  report_scope: 'final',
                  render_hint: 'markdown',
                },
                links: [
                  {
                    namespace: 'default',
                    workflow_id: 'test-123',
                    run_id: '01-run',
                    link_type: 'report.summary',
                    label: 'Summary',
                    created_at: '2026-03-28T00:00:04Z',
                  },
                  {
                    namespace: 'default',
                    workflow_id: 'test-123',
                    run_id: '01-run',
                    link_type: 'report.primary',
                    label: 'Final report',
                    created_at: '2026-03-28T00:00:03Z',
                  },
                ],
                default_read_ref: {
                  artifact_ref_v: 1,
                  artifact_id: 'art-report-preview',
                  content_type: 'text/markdown',
                  encryption: 'none',
                },
              },
            ],
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifact_id: 'art-report-primary',
                content_type: 'text/markdown',
                size_bytes: 2048,
                status: 'complete',
                metadata: { title: 'Final implementation report', render_hint: 'markdown' },
                links: [{ link_type: 'report.primary', label: 'Final report' }],
              },
              {
                artifact_id: 'art-report-summary',
                content_type: 'application/json',
                size_bytes: 512,
                status: 'complete',
                metadata: { title: 'Summary JSON' },
                links: [{ link_type: 'report.summary', label: 'Summary' }],
              },
              {
                artifact_id: 'art-report-evidence',
                content_type: 'image/png',
                size_bytes: 1024,
                status: 'complete',
                metadata: { title: 'Screenshot evidence' },
                links: [{ link_type: 'report.evidence', label: 'Screenshot' }],
              },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Report' })).toBeTruthy();
      expect(screen.getByText('Final implementation report')).toBeTruthy();
      expect(screen.getByText('Summary JSON')).toBeTruthy();
      expect(screen.getByText('Screenshot evidence')).toBeTruthy();
    });

    const reportHeading = screen.getByRole('heading', { name: 'Report' });
    const artifactsHeading = screen.getByRole('heading', { name: 'Artifacts' });
    expect(
      reportHeading.compareDocumentPosition(artifactsHeading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open report' }).getAttribute('href')).toBe(
      '/api/artifacts/art-report-preview/download',
    );
    expect(screen.getByText('markdown')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Summary' }).getAttribute('href')).toBe(
      '/api/artifacts/art-report-summary/download',
    );
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/executions/default/test-123/01-run/artifacts?link_type=report.primary&latest_only=true',
    );
  });

  it('does not fabricate report status when no primary report is returned', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Generic artifact task',
      summary: 'No report',
      status: 'completed',
      state: 'succeeded',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifact_id: 'art-generic-output',
                content_type: 'text/plain',
                size_bytes: 128,
                status: 'complete',
                metadata: { title: 'Looks report-ish' },
              },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Generic artifact task')).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Artifacts' })).toBeTruthy();
      expect(screen.getByText('art-generic-output')).toBeTruthy();
    });
    expect(screen.queryByRole('heading', { name: 'Report' })).toBeNull();
    expect(screen.queryByText('Looks report-ish')).toBeNull();
  });

  it('renders error state on failed fetch', async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: false,
      statusText: 'Not Found',
      json: async () => ({}),
    } as Response);

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch task: Not Found/i)).toBeTruthy();
    });
  });

  it('decodes encoded task ids from the route before fetching', async () => {
    window.history.pushState({}, 'Encoded Test', '/tasks/mm%3Atest-123?source=temporal');

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          taskId: 'mm:test-123',
          workflowId: 'mm:test-123',
          namespace: 'default',
          temporalRunId: '01-run',
          runId: '01-run',
          source: 'temporal',
          title: 'Encoded task',
          summary: 'Decoded route id',
          status: 'completed',
          state: 'succeeded',
          createdAt: '2026-03-28T00:00:00Z',
          updatedAt: '2026-03-28T00:00:02Z',
          actions: {},
        }),
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Encoded task')).toBeTruthy();
      expect(screen.getByText('Task mm:test-123')).toBeTruthy();
    });

    expect(fetchSpy).toHaveBeenCalledWith('/api/executions/mm%3Atest-123?source=temporal');
  });

  it('shows launch-waiting message when no taskRunId is present yet', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: 'non-uuid-run',
      runId: 'non-uuid-run',
      source: 'temporal',
      title: 'Running task',
      summary: 'In progress',
      status: 'running',
      state: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:00Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(
        screen.getByText(/Waiting for managed runtime launch to create live logs/i),
      ).toBeTruthy();
    });
  });

  it('renders artifact download link using explicit downloadUrl when present', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Artifact task with download_url',
      summary: 'Artifact payload',
      status: 'running',
      state: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifact_id: 'art-with-url',
                content_type: 'application/json',
                size_bytes: 1024,
                status: 'complete',
                download_url: 'https://external-storage.com/art-with-url',
              },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Artifact task with download_url')).toBeTruthy();
      expect(screen.getByText('art-with-url')).toBeTruthy();
      expect(screen.getByRole('link', { name: /Download/i }).getAttribute('href')).toBe(
        'https://external-storage.com/art-with-url'
      );
    });
  });

  it('groups task image inputs by persisted target and preserves download when preview fails', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Image input task',
      summary: 'Review uploaded inputs',
      status: 'running',
      state: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifact_id: 'art-objective',
                content_type: 'image/png',
                size_bytes: 1234,
                status: 'complete',
                download_url: 'https://storage.example/objective.png',
                metadata: {
                  source: 'task-dashboard-objective-attachment',
                  target: 'objective',
                  filename: 'objective.png',
                },
              },
              {
                artifact_id: 'art-step',
                content_type: 'image/webp',
                size_bytes: 4567,
                status: 'complete',
                download_url: 'https://storage.example/step.webp',
                metadata: {
                  source: 'task-dashboard-step-attachment',
                  stepLabel: 'Step 2',
                  filename: 'step.webp',
                },
              },
              {
                artifact_id: 'art-step-second',
                content_type: 'image/jpeg',
                size_bytes: 7890,
                status: 'complete',
                download_url: 'https://storage.example/step-second.jpg',
                metadata: {
                  source: 'task-dashboard-step-attachment',
                  stepLabel: 'Step 2',
                  filename: 'step-second.jpg',
                },
              },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Input Images' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Objective' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Step 2' })).toBeTruthy();
      expect(screen.getAllByRole('heading', { name: 'Step 2' })).toHaveLength(1);
      expect(screen.getByText('objective.png')).toBeTruthy();
      expect(screen.getByText('step.webp')).toBeTruthy();
      expect(screen.getByText('step-second.jpg')).toBeTruthy();
    });

    const objectivePreview = screen.getByAltText('Preview of Objective attachment objective.png');
    expect(objectivePreview.getAttribute('src')).toBe('/api/artifacts/art-objective/download');
    fireEvent.error(objectivePreview);

    await waitFor(() => {
      expect(
        screen.getByText(
          'Objective: Preview unavailable for objective.png. Attachment metadata remains available.',
        ),
      ).toBeTruthy();
    });

    const imageDownloadLinks = screen
      .getAllByRole('link', { name: 'Download' })
      .filter((link) => link.getAttribute('download'));
    expect(imageDownloadLinks.map((link) => link.getAttribute('href'))).toEqual([
      '/api/artifacts/art-objective/download',
      '/api/artifacts/art-step/download',
      '/api/artifacts/art-step-second/download',
    ]);
  });

  it('renders separate Intervention and Observation sections with intervention audit history', async () => {
    const actionPayload: BootPayload = {
      ...mockPayload,
      initialData: { dashboardConfig: { features: { temporalDashboard: { actionsEnabled: true } } } },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Intervention task',
      summary: 'Awaiting operator action',
      status: 'waiting',
      state: 'awaiting_external',
      rawState: 'awaiting_external',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canPause: true,
        canResume: true,
        canApprove: true,
        canCancel: true,
        canReject: true,
        canSendMessage: true,
      },
      interventionAudit: [
        {
          action: 'pause',
          transport: 'temporal_signal',
          summary: 'Pause requested.',
          createdAt: '2026-03-28T00:00:05Z',
        },
      ],
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Intervention' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Observation' })).toBeTruthy();
      expect(screen.getByText(/Pause requested\./)).toBeTruthy();
      expect(screen.getByText(/Live logs are passive observation only/i)).toBeTruthy();
    });
  });

  it('routes Pause through the explicit signal endpoint without requiring live log fetches', async () => {
    const actionPayload: BootPayload = {
      ...mockPayload,
      initialData: { dashboardConfig: { features: { temporalDashboard: { actionsEnabled: true } } } },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Pause task',
      summary: 'Running',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canPause: true,
      },
      interventionAudit: [],
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      if (url.endsWith('/signal')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...mockExecution,
            state: 'awaiting_external',
            rawState: 'awaiting_external',
            summary: 'Execution paused.',
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Pause' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/signal',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            signalName: 'Pause',
            payload: {},
          }),
        }),
      );
    });

    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/observability-summary')),
    ).toBe(false);
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/logs/stream')),
    ).toBe(false);
  });

  it('supports explicit send-message and reject controls outside the log viewer', async () => {
    const actionPayload: BootPayload = {
      ...mockPayload,
      initialData: { dashboardConfig: { features: { temporalDashboard: { actionsEnabled: true } } } },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Awaiting reply',
      summary: 'Need operator guidance',
      status: 'waiting',
      state: 'awaiting_external',
      rawState: 'awaiting_external',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canApprove: true,
        canResume: true,
        canCancel: true,
        canReject: true,
        canSendMessage: true,
      },
      interventionAudit: [],
    };

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      if (url.endsWith('/signal')) {
        return Promise.resolve({
          ok: true,
          json: async () => mockExecution,
        } as Response);
      }
      if (url.endsWith('/cancel')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...mockExecution,
            state: 'canceled',
            rawState: 'canceled',
            summary: 'Rejected by operator.',
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionPayload} />);

    fireEvent.change(await screen.findByLabelText('Operator message'), {
      target: { value: 'Please use Provider Profiles.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send Message' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/signal',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            signalName: 'SendMessage',
            payload: { message: 'Please use Provider Profiles.' },
          }),
        }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/cancel',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            action: 'reject',
            graceful: true,
            reason: 'Rejected by operator.',
          }),
        }),
      );
    });

    confirmSpy.mockRestore();
  });

  it('does not show the obsolete dependency wait skip action in the Intervention panel', async () => {
    const actionPayload: BootPayload = {
      ...mockPayload,
      initialData: { dashboardConfig: { features: { temporalDashboard: { actionsEnabled: true } } } },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Blocked dependent task',
      summary: 'Waiting on prerequisites',
      status: 'waiting',
      state: 'waiting_on_dependencies',
      rawState: 'waiting_on_dependencies',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      blockedOnDependencies: true,
      dependencies: [{ workflowId: 'dep-1', title: 'Prerequisite one', state: 'running' }],
      dependents: [],
      actions: {
        canCancel: true,
        canSkipDependencyWait: true,
      },
      interventionAudit: [],
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={actionPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy();
    });
    expect(screen.queryByRole('button', { name: 'Skip Dependency Wait' })).toBeNull();
  });

  it('renders a Session Continuity panel for Codex managed-session task runs', async () => {
    const codexPayload: BootPayload = {
      ...mockPayload,
      initialData: { dashboardConfig: { features: { temporalDashboard: { actionsEnabled: true } } } },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Codex session task',
      summary: 'Session-backed work',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      targetRuntime: 'codex',
      taskRunId: 'wf-task-1',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canCancel: true,
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifact-sessions/sess%3Awf-task-1%3Acodex_cli')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            task_run_id: 'wf-task-1',
            session_id: 'sess:wf-task-1:codex_cli',
            session_epoch: 2,
            grouped_artifacts: [
              {
                group_key: 'continuity',
                title: 'Continuity',
                artifacts: [
                  { artifact_id: 'art-summary', status: 'complete' },
                  { artifact_id: 'art-checkpoint', status: 'complete' },
                ],
              },
              {
                group_key: 'control',
                title: 'Control',
                artifacts: [
                  { artifact_id: 'art-control', status: 'complete' },
                  { artifact_id: 'art-reset', status: 'complete' },
                ],
              },
            ],
            latest_summary_ref: { artifact_id: 'art-summary' },
            latest_checkpoint_ref: { artifact_id: 'art-checkpoint' },
            latest_control_event_ref: { artifact_id: 'art-control' },
            latest_reset_boundary_ref: { artifact_id: 'art-reset' },
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={codexPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Session Continuity' })).toBeTruthy();
      expect(screen.getByText(/Epoch 2/)).toBeTruthy();
      expect(screen.getAllByText('art-summary').length).toBeGreaterThan(0);
      expect(screen.getAllByText('art-checkpoint').length).toBeGreaterThan(0);
      expect(screen.getAllByText('art-control').length).toBeGreaterThan(0);
      expect(screen.getAllByText('art-reset').length).toBeGreaterThan(0);
      expect(screen.getByText('Live Logs')).toBeTruthy();
      expect(screen.getByText('Diagnostics')).toBeTruthy();
    });
  });

  it('explains Live Logs as timeline history and Session Continuity as durable drill-down evidence', async () => {
    const codexPayload: BootPayload = {
      ...mockPayload,
      initialData: {
        dashboardConfig: {
          features: {
            logStreamingEnabled: true,
            liveLogsSessionTimelineEnabled: true,
          },
        },
      },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Codex session task',
      summary: 'Session-backed work',
      status: 'completed',
      state: 'succeeded',
      rawState: 'succeeded',
      targetRuntime: 'codex_cli',
      taskRunId: 'wf-task-1',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canCancel: true,
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'completed',
              supportsLiveStreaming: false,
              liveStreamStatus: 'ended',
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/artifact-sessions/sess%3Awf-task-1%3Acodex_cli')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            task_run_id: 'wf-task-1',
            session_id: 'sess:wf-task-1:codex_cli',
            session_epoch: 2,
            grouped_artifacts: [
              {
                group_key: 'continuity',
                title: 'Continuity',
                artifacts: [{ artifact_id: 'art-summary', status: 'complete' }],
              },
            ],
            latest_summary_ref: { artifact_id: 'art-summary' },
            latest_checkpoint_ref: null,
            latest_control_event_ref: null,
            latest_reset_boundary_ref: null,
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={codexPayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/timeline shows what happened/i)).toBeTruthy();
      expect(screen.getByText(/durable evidence and drill-down/i)).toBeTruthy();
    });
  });

  it('routes Session Continuity follow-up and reset controls through the task-run session control API', async () => {
    const codexPayload: BootPayload = {
      ...mockPayload,
      initialData: { dashboardConfig: { features: { temporalDashboard: { actionsEnabled: true } } } },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'wf-session-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Codex session task',
      summary: 'Session-backed work',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      targetRuntime: 'codex_cli',
      taskRunId: 'wf-task-1',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canCancel: true,
      },
    };
    const executionDetailUrl = '/api/executions/test-123?source=temporal';

    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            action: JSON.parse(String(init?.body || '{}')).action,
            projection: {
              task_run_id: 'wf-task-1',
              session_id: 'sess:wf-task-1:codex_cli',
              session_epoch: JSON.parse(String(init?.body || '{}')).action === 'clear_session' ? 2 : 1,
              grouped_artifacts: [],
              latest_summary_ref: { artifact_id: 'art-summary' },
              latest_checkpoint_ref: { artifact_id: 'art-checkpoint' },
              latest_control_event_ref: { artifact_id: 'art-control' },
              latest_reset_boundary_ref:
                JSON.parse(String(init?.body || '{}')).action === 'clear_session'
                  ? { artifact_id: 'art-reset' }
                  : null,
            },
          }),
        } as Response);
      }
      if (url.includes('/artifact-sessions/sess%3Awf-task-1%3Acodex_cli')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            task_run_id: 'wf-task-1',
            session_id: 'sess:wf-task-1:codex_cli',
            session_epoch: 1,
            grouped_artifacts: [],
            latest_summary_ref: { artifact_id: 'art-summary' },
            latest_checkpoint_ref: { artifact_id: 'art-checkpoint' },
            latest_control_event_ref: null,
            latest_reset_boundary_ref: null,
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={codexPayload} />);

    fireEvent.change(await screen.findByLabelText('Follow-up message'), {
      target: { value: 'Continue with the existing session.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send follow-up' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/task-runs/wf-task-1/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            action: 'send_follow_up',
            message: 'Continue with the existing session.',
          }),
        }),
      );
    });
    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.filter(([input]) => String(input) === executionDetailUrl).length,
      ).toBeGreaterThan(1);
    });

    fireEvent.click(screen.getByRole('button', { name: 'Clear / Reset' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/task-runs/wf-task-1/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            action: 'clear_session',
          }),
        }),
      );
    });
  });

  it('reuses the existing execution cancel route from the Session Continuity panel', async () => {
    const codexPayload: BootPayload = {
      ...mockPayload,
      initialData: { dashboardConfig: { features: { temporalDashboard: { actionsEnabled: true } } } },
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      title: 'Codex session task',
      summary: 'Session-backed work',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      targetRuntime: 'codex_cli',
      taskRunId: 'wf-task-1',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canCancel: true,
      },
    };

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/cancel')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...mockExecution,
            state: 'canceled',
            rawState: 'canceled',
          }),
        } as Response);
      }
      if (url.includes('/artifact-sessions/sess%3Awf-task-1%3Acodex_cli')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            task_run_id: 'wf-task-1',
            session_id: 'sess:wf-task-1:codex_cli',
            session_epoch: 1,
            grouped_artifacts: [],
            latest_summary_ref: null,
            latest_checkpoint_ref: null,
            latest_control_event_ref: null,
            latest_reset_boundary_ref: null,
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ artifacts: [] }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={codexPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Cancel Execution' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/cancel',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            action: 'cancel',
            graceful: true,
          }),
        }),
      );
    });

    confirmSpy.mockRestore();
  });

  it('keeps polling session continuity until a projection or terminal state exists', () => {
    expect(getSessionProjectionRefetchInterval(false, false, false)).toBe(5000);
    expect(getSessionProjectionRefetchInterval(false, true, false)).toBe(false);
    expect(getSessionProjectionRefetchInterval(false, false, true)).toBe(false);
    expect(getSessionProjectionRefetchInterval(true, false, false)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// LiveLogsPanel — full lifecycle tests via TaskDetailPage
// ---------------------------------------------------------------------------

describe('LiveLogsPanel', () => {
  const mockPayload: BootPayload = { page: 'task-detail', apiBase: '/api' };
  const fastPollPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        pollIntervalsMs: { detail: 1 },
      },
    },
  };

  const activeExecution = {
    taskId: 'wf-1',
    workflowId: 'wf-1',
    namespace: 'default',
    temporalRunId: '01-run',
    runId: '01-run',
    source: 'temporal',
    title: 'Active task',
    summary: 'Running',
    status: 'running',
    state: 'executing',
    rawState: 'executing',
    taskRunId: '550e8400-e29b-41d4-a716-446655440000',
    createdAt: '2026-03-28T00:00:00Z',
    updatedAt: '2026-03-28T00:00:02Z',
    actions: {},
  };

  const terminalExecution = {
    ...activeExecution,
    status: 'completed',
    state: 'succeeded',
    rawState: 'succeeded',
  };
  const codexExecution = {
    ...activeExecution,
    targetRuntime: 'codex_cli',
  };
  const geminiExecution = {
    ...activeExecution,
    targetRuntime: 'gemini_cli',
  };
  const sessionTimelinePayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        features: {
          logStreamingEnabled: true,
          liveLogsSessionTimelineEnabled: true,
        },
      },
    },
  };
  const codexManagedRolloutPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        features: {
          logStreamingEnabled: true,
          liveLogsSessionTimelineEnabled: true,
          liveLogsSessionTimelineRollout: 'codex_managed',
        },
      },
    },
  };
  const allManagedRolloutPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        features: {
          logStreamingEnabled: true,
          liveLogsSessionTimelineEnabled: true,
          liveLogsSessionTimelineRollout: 'all_managed',
        },
      },
    },
  };
  const rolloutOffPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        features: {
          logStreamingEnabled: true,
          liveLogsSessionTimelineEnabled: true,
          liveLogsSessionTimelineRollout: 'off',
        },
      },
    },
  };
  const legacyLiveLogsPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        features: {
          logStreamingEnabled: true,
          liveLogsSessionTimelineEnabled: false,
        },
      },
    },
  };
  const structuredHistoryDisabledPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        features: {
          logStreamingEnabled: true,
          liveLogsSessionTimelineEnabled: true,
          liveLogsStructuredHistoryEnabled: false,
        },
      },
    },
  };

  const activeSummary = {
    summary: {
      status: 'running',
      supportsLiveStreaming: true,
      liveStreamStatus: 'available',
    },
  };

  const endedSummary = {
    summary: {
      status: 'completed',
      supportsLiveStreaming: false,
      liveStreamStatus: 'ended',
    },
  };

  const noStreamSummary = {
    summary: {
      status: 'running',
      supportsLiveStreaming: false,
      liveStreamStatus: 'unavailable',
    },
  };

  let fetchSpy: MockInstance;
  let originalEventSource: typeof EventSource;
  let originalClipboardDescriptor: PropertyDescriptor | undefined;
  let originalDocumentHiddenDescriptor: PropertyDescriptor | undefined;
  let originalScrollIntoViewDescriptor: PropertyDescriptor | undefined;

  beforeEach(() => {
    window.history.pushState({}, 'Test', '/tasks/wf-1?source=temporal');
    window.sessionStorage.clear();
    fetchSpy = vi.spyOn(window, 'fetch');
    MockEventSource.reset();
    originalEventSource = window.EventSource;
    originalClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    originalDocumentHiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');
    originalScrollIntoViewDescriptor = Object.getOwnPropertyDescriptor(
      Element.prototype,
      'scrollIntoView',
    );
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).EventSource = MockEventSource;
    if (typeof Element.prototype.scrollIntoView !== 'function') {
      Object.defineProperty(Element.prototype, 'scrollIntoView', {
        configurable: true,
        writable: true,
        value: vi.fn(),
      });
    }
  });

  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).EventSource = originalEventSource;
    if (originalClipboardDescriptor) {
      Object.defineProperty(navigator, 'clipboard', originalClipboardDescriptor);
    } else {
      Reflect.deleteProperty(navigator, 'clipboard');
    }
    if (originalDocumentHiddenDescriptor) {
      Object.defineProperty(document, 'hidden', originalDocumentHiddenDescriptor);
    } else {
      Reflect.deleteProperty(document, 'hidden');
    }
    if (originalScrollIntoViewDescriptor) {
      Object.defineProperty(Element.prototype, 'scrollIntoView', originalScrollIntoViewDescriptor);
    } else {
      Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
    }
    vi.restoreAllMocks();
  });

  /** Build a fetch mock that routes execution, artifacts, observability, and merged tail calls. */
  function mockFetchSequence(
    execution: object,
    summary: object,
    tailContent: string = '',
  ) {
    const buildHistoryPayload = () => {
      const lines = tailContent.split('\n');
      const events: Array<{ sequence: number; timestamp: string; stream: string; text: string }> = [];
      let currentStream = 'stdout';
      let sequence = 1;
      let buffer: string[] = [];

      const flushBuffer = () => {
        if (buffer.length === 0) return;
        events.push({
          sequence,
          timestamp: `2026-04-08T00:00:${String(sequence).padStart(2, '0')}Z`,
          stream: currentStream,
          text: `${buffer.join('\n')}\n`,
        });
        sequence += 1;
        buffer = [];
      };

      for (const line of lines) {
        if (line === '--- stdout ---') {
          flushBuffer();
          currentStream = 'stdout';
          continue;
        }
        if (line === '--- stderr ---') {
          flushBuffer();
          currentStream = 'stderr';
          continue;
        }
        if (line === '--- system ---') {
          flushBuffer();
          currentStream = 'system';
          continue;
        }
        if (line.length === 0) continue;
        buffer.push(line);
      }
      flushBuffer();

      if (events.length === 0 && tailContent) {
        events.push({
          sequence: 1,
          timestamp: '2026-04-08T00:00:01Z',
          stream: 'stdout',
          text: tailContent,
        });
      }

      return { events, truncated: false };
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({ ok: true, json: async () => summary } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => buildHistoryPayload(),
        } as Response);
      }
      if (url.includes('/logs/merged')) {
        return Promise.resolve({
          ok: true,
          text: async () => tailContent,
        } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => execution } as Response);
    });
  }

  it('shows Loading then Connected status after artifact tail + SSE connect', async () => {
    mockFetchSequence(activeExecution, activeSummary, '');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    // Initial state: Loading
    await waitFor(() => expect(screen.getByText(/Loading…/)).toBeTruthy());

    // After fetch sequence completes, SSE is created and can be opened
    await waitForEventSourceInstance();
    const es = MockEventSource.instances.at(-1)!;

    act(() => es.triggerOpen());
    await waitFor(() => expect(screen.getByText(/Connected/)).toBeTruthy());
  });

  it('shows artifact tail content before SSE connects', async () => {
    mockFetchSequence(activeExecution, activeSummary, 'artifact line 1\nartifact line 2\n');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => expect(screen.getByText(/artifact line 1/)).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/artifact line 2/)).toBeTruthy());
    await waitFor(() => {
      expect(document.querySelectorAll('[data-stream]').length).toBe(2);
    });
  });

  it('appends log_chunk text from SSE after artifact tail is shown', async () => {
    mockFetchSequence(activeExecution, activeSummary, 'first from artifact\n');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitForEventSourceInstance();
    const es = MockEventSource.instances.at(-1)!;
    expect(es.url).toContain('since=1');

    act(() => es.triggerOpen());
    act(() => es.triggerLogChunk({ sequence: 0, stream: 'stdout', text: 'live line\n' }));

    await waitFor(() => {
      expect(screen.getByText(/first from artifact/)).toBeTruthy();
      expect(screen.getByText(/live line/)).toBeTruthy();
    });
    expect(document.querySelectorAll('[data-stream]').length).toBe(2);
  });

  it('does not auto-scroll the page when live logs update', async () => {
    const scrollIntoViewSpy = vi
      .spyOn(Element.prototype, 'scrollIntoView')
      .mockImplementation(() => {});
    try {
      mockFetchSequence(activeExecution, activeSummary, 'first from artifact\n');
      renderWithClient(<TaskDetailPage payload={mockPayload} />);

      fireEvent.click(await screen.findByText('Live Logs'));

      await waitForEventSourceInstance();
      const es = MockEventSource.instances[0]!;
      scrollIntoViewSpy.mockClear();

      act(() => es.triggerOpen());
      act(() => es.triggerLogChunk({ sequence: 1, stream: 'stdout', text: 'live line\n' }));

      await waitFor(() => {
        expect(screen.getByText(/live line/)).toBeTruthy();
      });
      expect(scrollIntoViewSpy).not.toHaveBeenCalled();
    } finally {
      scrollIntoViewSpy.mockRestore();
    }
  });

  it('does not create EventSource for ended runs', async () => {
    mockFetchSequence(terminalExecution, endedSummary, 'final output\n');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => expect(screen.getByText(/Stream ended/)).toBeTruthy(), { timeout: 5000 });
    expect(MockEventSource.instances.length).toBe(0);
  });

  it('does not create EventSource when supportsLiveStreaming is false', async () => {
    mockFetchSequence(activeExecution, noStreamSummary, 'artifact-only content\n');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    // No SSE should be created; panel shows artifact content
    await waitFor(() => expect(screen.getByText(/artifact-only content/)).toBeTruthy());
    expect(MockEventSource.instances.length).toBe(0);
  });

  it('falls back to merged logs when structured history returns 404', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({ ok: true, json: async () => noStreamSummary } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({ ok: false, status: 404 } as Response);
      }
      if (url.includes('/logs/merged')) {
        return Promise.resolve({
          ok: true,
          text: async () => 'merged fallback line\n',
        } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => activeExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => expect(screen.getByText(/merged fallback line/)).toBeTruthy());
    expect(MockEventSource.instances.length).toBe(0);
  });

  it('closes stream and shows Stream ended when task transitions to terminal', async () => {
    let currentExecution = activeExecution;
    let currentSummary = activeSummary;
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({ ok: true, json: async () => currentSummary } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({ ok: true, json: async () => ({ events: [], truncated: false }) } as Response);
      }
      if (url.includes('/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => '' } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => currentExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitForEventSourceInstance();
    const es = MockEventSource.instances.at(-1)!;
    act(() => es.triggerOpen());

    // Transition to terminal state on next poll
    currentExecution = terminalExecution;
    currentSummary = endedSummary;
    await waitFor(() => expect(screen.getByText(/Stream ended/)).toBeTruthy(), { timeout: 5000 });
    expect(es.closed).toBe(true);
  });

  it('shows Disconnected and artifact backup when SSE onerror fires on non-terminal task', async () => {
    mockFetchSequence(activeExecution, activeSummary, 'artifact backup content\n');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitForEventSourceInstance();
    const es = MockEventSource.instances[0]!;
    act(() => es.triggerOpen());
    act(() => es.triggerError());

    await waitFor(() => expect(screen.getByText(/Disconnected — showing artifact backup/)).toBeTruthy());
    // Artifact content should still be visible
    await waitFor(() => expect(screen.getByText(/artifact backup content/)).toBeTruthy());
  });
  it('defaults to collapsed and does not fetch observability data until expanded', async () => {
    fetchSpy.mockClear();
    mockFetchSequence(activeExecution, activeSummary, 'backup');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    // Wait until the initial execute fetch finishes so task is loaded
    await waitFor(() => expect(screen.getByText('Active task')).toBeTruthy());

    // Before click, it shouldn't have fetched the summary
    expect(fetchSpy).not.toHaveBeenCalledWith(expect.stringContaining('/observability-summary'), expect.anything());

    fireEvent.click(await screen.findByText('Live Logs'));

    // Now it should fetch
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(expect.stringContaining('/observability-summary'), expect.anything());
    });
  });

  it('closes EventSource when the panel is collapsed', async () => {
    mockFetchSequence(activeExecution, activeSummary, 'backup');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    // Open it
    fireEvent.click(await screen.findByText('Live Logs'));
    
    // Wait for SSE to connect
    await waitForEventSourceInstance();
    const es = MockEventSource.instances[0]!;
    
    // Collapse it
    fireEvent.click(await screen.findByText('Live Logs'));

    // Ensure it closed
    await waitFor(() => expect(es.closed).toBe(true));
  });

  it('closes EventSource when the page is hidden, reconnects when visible', async () => {
    mockFetchSequence(activeExecution, activeSummary, 'backup');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    // Open it
    fireEvent.click(await screen.findByText('Live Logs'));
    
    // Wait for SSE to connect
    await waitForEventSourceInstance();
    const es1 = MockEventSource.instances[0]!;
    
    // Hide visibility
    act(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      document.dispatchEvent(new Event('visibilitychange'));
    });

    // Ensure it closed current stream
    await waitFor(() => expect(es1.closed).toBe(true));

    // Show visibility
    act(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
      document.dispatchEvent(new Event('visibilitychange'));
    });

    // Ensure it opened a NEW stream
    await waitFor(() => expect(MockEventSource.instances.length).toBe(2));
    expect(MockEventSource.instances[1]!.closed).toBe(false);
  });

  it('shows per-line stream provenance (stdout, stderr, system)', async () => {
    mockFetchSequence(activeExecution, activeSummary, '--- stdout ---\nline 1\n--- stderr ---\nline 2');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));
    
    // Wait for artifact backing
    await waitFor(() => {
      expect(screen.getByText('line 1')).toBeTruthy();
      expect(screen.getByText('line 2')).toBeTruthy();
    });

    // Check DOM for provenance attributes
    const stdoutLine = screen.getByText('line 1').closest('div');
    expect(stdoutLine?.getAttribute('data-stream')).toBe('stdout');

    const stderrLine = screen.getByText('line 2').closest('div');
    expect(stderrLine?.getAttribute('data-stream')).toBe('stderr');

    // Simulate SSE chunk for system
    await waitForEventSourceInstance();
    const es = MockEventSource.instances[0]!;
    act(() => es.triggerOpen());
    
    act(() => {
      es.triggerLogChunk({ sequence: 10, text: 'system event\n', stream: 'system' });
    });

    await waitFor(() => {
      expect(screen.getByText('system event')).toBeTruthy();
    });

    const systemLine = screen.getByText('system event').closest('div');
    expect(systemLine?.getAttribute('data-stream')).toBe('system');
  });

  it('renders session reset boundaries as explicit timeline rows with session badges', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'completed',
              supportsLiveStreaming: false,
              liveStreamStatus: 'ended',
              sessionSnapshot: {
                sessionId: 'sess:wf-task-1:codex_cli',
                sessionEpoch: 2,
                containerId: 'ctr-1',
                threadId: 'thread-2',
                activeTurnId: null,
              },
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-08T00:00:01Z',
                stream: 'session',
                text: 'Epoch boundary reached. Session sess:wf-task-1:codex_cli is now on epoch 2 thread thread-2.',
                kind: 'session_reset_boundary',
                session_id: 'sess:wf-task-1:codex_cli',
                session_epoch: 2,
                thread_id: 'thread-2',
              },
            ],
            truncated: false,
            sessionSnapshot: {
              sessionId: 'sess:wf-task-1:codex_cli',
              sessionEpoch: 2,
              containerId: 'ctr-1',
              threadId: 'thread-2',
              activeTurnId: null,
            },
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => terminalExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/Epoch boundary reached/)).toBeTruthy();
      expect(screen.getByText('sess:wf-task-1:codex_cli')).toBeTruthy();
      expect(screen.getByText('thread-2')).toBeTruthy();
    });

    const boundaryRow = screen.getByText(/Epoch boundary reached/).closest('div');
    expect(boundaryRow?.getAttribute('data-kind')).toBe('session_reset_boundary');
    expect(boundaryRow?.getAttribute('data-stream')).toBe('session');
  });

  it('renders the session-aware timeline header with container and live status when the feature flag is enabled', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: true,
              liveStreamStatus: 'live',
              sessionSnapshot: {
                sessionId: 'sess:wf-task-1:codex_cli',
                sessionEpoch: 4,
                containerId: 'ctr-99',
                threadId: 'thread-4',
                activeTurnId: 'turn-7',
              },
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-08T00:00:01Z',
                stream: 'session',
                kind: 'turn_started',
                text: 'Turn started',
                session_id: 'sess:wf-task-1:codex_cli',
                session_epoch: 4,
                container_id: 'ctr-99',
                thread_id: 'thread-4',
                active_turn_id: 'turn-7',
              },
            ],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => activeExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={sessionTimelinePayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText('sess:wf-task-1:codex_cli')).toBeTruthy();
      expect(screen.getByText('4')).toBeTruthy();
      expect(screen.getByText('ctr-99')).toBeTruthy();
      expect(screen.getByText('thread-4')).toBeTruthy();
      expect(screen.getByText('turn-7')).toBeTruthy();
      expect(screen.getByText('live')).toBeTruthy();
    });
  });

  it('renders distinct timeline row types for approval and publication events when the feature flag is enabled', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'completed',
              supportsLiveStreaming: false,
              liveStreamStatus: 'ended',
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-08T00:00:01Z',
                stream: 'session',
                kind: 'approval_requested',
                text: 'Approval requested for command execution.',
              },
              {
                sequence: 2,
                timestamp: '2026-04-08T00:00:02Z',
                stream: 'session',
                kind: 'summary_published',
                text: 'Session summary artifact published.',
              },
            ],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => terminalExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={sessionTimelinePayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/Approval requested for command execution/)).toBeTruthy();
      expect(screen.getByText(/Session summary artifact published/)).toBeTruthy();
    });

    const approvalRow = screen.getByText(/Approval requested for command execution/).closest('div');
    const publicationRow = screen.getByText(/Session summary artifact published/).closest('div');
    expect(approvalRow?.getAttribute('data-row-type')).toBe('approval');
    expect(publicationRow?.getAttribute('data-row-type')).toBe('publication');
  });

  it('renders inline artifact links for publication and clear-reset timeline rows', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'completed',
              supportsLiveStreaming: false,
              liveStreamStatus: 'ended',
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-08T00:00:01Z',
                stream: 'session',
                kind: 'summary_published',
                text: 'Session summary artifact published.',
                metadata: {
                  summaryRef: 'art-summary',
                },
              },
              {
                sequence: 2,
                timestamp: '2026-04-08T00:00:02Z',
                stream: 'session',
                kind: 'checkpoint_published',
                text: 'Session checkpoint artifact published.',
                metadata: {
                  checkpointRef: 'art-checkpoint',
                },
              },
              {
                sequence: 3,
                timestamp: '2026-04-08T00:00:03Z',
                stream: 'session',
                kind: 'session_cleared',
                text: 'Session cleared.',
                metadata: {
                  controlEventRef: 'art-control',
                  resetBoundaryRef: 'art-reset',
                },
              },
              {
                sequence: 4,
                timestamp: '2026-04-08T00:00:04Z',
                stream: 'session',
                kind: 'session_reset_boundary',
                text: 'Epoch boundary reached.',
                metadata: {
                  controlEventRef: 'art-control',
                  resetBoundaryRef: 'art-reset',
                },
              },
            ],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => terminalExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={sessionTimelinePayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByRole('link', { name: /open summary artifact/i })).toBeTruthy();
      expect(screen.getByRole('link', { name: /open checkpoint artifact/i })).toBeTruthy();
      expect(screen.getAllByRole('link', { name: /open control event artifact/i }).length).toBeGreaterThan(0);
      expect(screen.getAllByRole('link', { name: /open reset boundary artifact/i }).length).toBeGreaterThan(0);
    });

    expect(
      screen.getByRole('link', { name: /open summary artifact/i }).getAttribute('href'),
    ).toBe('/api/artifacts/art-summary/download');
    expect(
      screen.getByRole('link', { name: /open checkpoint artifact/i }).getAttribute('href'),
    ).toBe('/api/artifacts/art-checkpoint/download');
  });

  it('uses the legacy line viewer when the session timeline feature flag is disabled', async () => {
    mockFetchSequence(activeExecution, activeSummary, 'legacy fallback line\n');
    renderWithClient(<TaskDetailPage payload={legacyLiveLogsPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/legacy fallback line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-legacy-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-timeline-viewer')).toBeNull();
  });

  it('uses the legacy line viewer when the session timeline feature flag is absent', async () => {
    mockFetchSequence(activeExecution, activeSummary, 'legacy fallback line\n');
    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/legacy fallback line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-legacy-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-timeline-viewer')).toBeNull();
  });

  it('enables the session timeline for codex managed runs when rollout is codex_managed', async () => {
    mockFetchSequence(codexExecution, activeSummary, 'codex rollout line\n');
    renderWithClient(<TaskDetailPage payload={codexManagedRolloutPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/codex rollout line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-timeline-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-legacy-viewer')).toBeNull();
  });

  it('keeps non-codex runs on the legacy viewer when rollout is codex_managed', async () => {
    mockFetchSequence(geminiExecution, activeSummary, 'gemini rollout line\n');
    renderWithClient(<TaskDetailPage payload={codexManagedRolloutPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/gemini rollout line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-legacy-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-timeline-viewer')).toBeNull();
  });

  it('enables the session timeline for managed runs when rollout is all_managed', async () => {
    mockFetchSequence(geminiExecution, activeSummary, 'all managed line\n');
    renderWithClient(<TaskDetailPage payload={allManagedRolloutPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/all managed line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-timeline-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-legacy-viewer')).toBeNull();
  });

  it('prefers the legacy viewer when rollout is off even if the boolean flag is true', async () => {
    mockFetchSequence(codexExecution, activeSummary, 'rollout off line\n');
    renderWithClient(<TaskDetailPage payload={rolloutOffPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/rollout off line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-legacy-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-timeline-viewer')).toBeNull();
  });

  it('falls back to merged logs when structured history succeeds with zero events', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({ ok: true, json: async () => endedSummary } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ events: [], truncated: false }),
        } as Response);
      }
      if (url.includes('/logs/merged')) {
        return Promise.resolve({
          ok: true,
          text: async () => 'empty history fallback line\n',
        } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => terminalExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={sessionTimelinePayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/empty history fallback line/)).toBeTruthy();
    });
  });

  it('skips structured history and loads merged logs when structured history is disabled', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({ ok: true, json: async () => noStreamSummary } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({ ok: false, status: 500 } as Response);
      }
      if (url.includes('/logs/merged')) {
        return Promise.resolve({
          ok: true,
          text: async () => 'rollback merged history\n',
        } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => codexExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={structuredHistoryDisabledPayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/rollback merged history/)).toBeTruthy();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/observability/events')),
    ).toBe(false);
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/logs/merged')),
    ).toBe(true);
  });

  it('derives session badges from camelCase historical event metadata', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'completed',
              supportsLiveStreaming: false,
              liveStreamStatus: 'ended',
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-08T00:00:01Z',
                stream: 'session',
                kind: 'turn_started',
                text: 'Camel case turn started',
                sessionId: 'sess:wf-task-1:codex_cli',
                sessionEpoch: 5,
                containerId: 'ctr-camel',
                threadId: 'thread-camel',
                activeTurnId: 'turn-camel',
              },
            ],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => codexExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={sessionTimelinePayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText('sess:wf-task-1:codex_cli')).toBeTruthy();
      expect(screen.getByText('ctr-camel')).toBeTruthy();
      expect(screen.getByText('thread-camel')).toBeTruthy();
      expect(screen.getByText('turn-camel')).toBeTruthy();
    });
  });

  it('normalizes camelCase observability event metadata into the canonical session fields', () => {
    expect(
      normalizeObservabilityEvent({
        sequence: 11,
        timestamp: '2026-04-08T00:00:11Z',
        stream: 'session',
        text: 'Camel case SSE turn started',
        kind: 'turn_started',
        sessionId: 'sess:wf-task-1:codex_cli',
        sessionEpoch: 6,
        containerId: 'ctr-live-camel',
        threadId: 'thread-live-camel',
        activeTurnId: 'turn-live-camel',
      }),
    ).toMatchObject({
      session_id: 'sess:wf-task-1:codex_cli',
      session_epoch: 6,
      container_id: 'ctr-live-camel',
      thread_id: 'thread-live-camel',
      active_turn_id: 'turn-live-camel',
    });
  });

  it('renders the timeline viewer with ANSI-aware output when the session timeline feature flag is enabled', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'completed',
              supportsLiveStreaming: false,
              liveStreamStatus: 'ended',
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-08T00:00:01Z',
                stream: 'stdout',
                text: '\u001b[31mred output\u001b[0m\n',
              },
            ],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => terminalExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={sessionTimelinePayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText('red output')).toBeTruthy();
    });

    expect(virtuosoPropsSpy).toHaveBeenCalled();
    expect(virtuosoPropsSpy.mock.calls.at(-1)?.[0]?.initialItemCount).toBeUndefined();
    expect(screen.getByTestId('live-logs-timeline-viewer')).toBeTruthy();
    expect(screen.queryByText('\u001b[31mred output\u001b[0m')).toBeNull();
    expect(document.querySelector('[data-ansi-fragment="true"]')).toBeTruthy();
  });

  it('renders Stdout, Stderr, and Diagnostics panels', async () => {
    // Setup fetch mock to also return stdout, stderr, and diagnostics
    const stdoutContent = 'stdout line 1\nstdout line 2';
    const stderrContent = 'stderr error 1';
    const diagnosticsContent = '{"some": "json"}';

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/logs/stdout')) return Promise.resolve({ ok: true, text: async () => stdoutContent } as Response);
      if (url.includes('/logs/stderr')) return Promise.resolve({ ok: true, text: async () => stderrContent } as Response);
      if (url.includes('/diagnostics')) return Promise.resolve({ ok: true, text: async () => diagnosticsContent } as Response);
      if (url.includes('/observability/events')) return Promise.resolve({ ok: true, json: async () => ({ events: [], truncated: false }) } as Response);
      if (url.includes('/logs/merged')) return Promise.resolve({ ok: true, text: async () => '' } as Response);
      if (url.includes('/observability-summary')) return Promise.resolve({ ok: true, json: async () => ({ summary: { status: 'completed' } }) } as Response);
      if (url.includes('/artifacts')) return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      return Promise.resolve({
        ok: true,
        json: async () => ({
          taskId: 'test-123',
          workflowId: 'test-123',
          temporalRunId: '01-run',
          namespace: 'default',
          taskRunId: '123e4567-e89b-12d3-a456-426614174000',
          source: 'temporal',
          title: 'Mock task',
          summary: 'Mock summary',
          state: 'succeeded',
          status: 'completed',
          createdAt: '2026-03-28T00:00:00Z',
          updatedAt: '2026-03-28T00:00:02Z',
        }),
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    // Trigger expanding the panels
    fireEvent.click(await screen.findByText('Stdout'));
    fireEvent.click(await screen.findByText('Stderr'));
    fireEvent.click(await screen.findByText('Diagnostics'));

    await waitFor(() => {
      expect(screen.getByText(/stdout line 1/)).toBeTruthy();
      expect(screen.getByText(/stderr error 1/)).toBeTruthy();
      expect(screen.getByText(/\{"some": "json"\}/)).toBeTruthy();
    });
  });

  it('provides wrap toggle, copy support, and download affordances', async () => {
    const clipboardMock = { writeText: vi.fn() };
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: clipboardMock,
    });

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/logs/stdout')) return Promise.resolve({ ok: true, text: async () => 'stdout data' } as Response);
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-08T00:00:01Z',
                stream: 'stdout',
                text: 'live log data\n',
              },
            ],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/logs/merged')) return Promise.resolve({ ok: true, text: async () => 'live log data' } as Response);
      if (url.includes('/observability-summary')) return Promise.resolve({ ok: true, json: async () => ({ summary: { status: 'completed' } }) } as Response);
      return Promise.resolve({
        ok: true,
        json: async () => ({
          taskId: 'test-123',
          workflowId: 'test-123',
          temporalRunId: '01-run',
          namespace: 'default',
          taskRunId: 'mock-uuid-1',
          source: 'temporal',
          title: 'Mock task',
          summary: 'Mock summary',
          state: 'succeeded',
          status: 'completed',
          createdAt: '2026-03-28T00:00:00Z',
        }),
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    // Check Live Logs Panel
    fireEvent.click(await screen.findByText('Live Logs'));
    await waitFor(() => expect(screen.getByText(/live log data/)).toBeTruthy());

    // Toggle wrap
    const wrapCheckbox = screen.getAllByLabelText('Wrap lines')[0] as HTMLInputElement;
    expect(wrapCheckbox.checked).toBe(true);
    fireEvent.click(wrapCheckbox);
    expect(wrapCheckbox.checked).toBe(false);

    // Click copy
    const copyButton = screen.getAllByText('Copy')[0];
    fireEvent.click(copyButton!);
    expect(clipboardMock.writeText).toHaveBeenCalledWith(expect.stringContaining('live log data'));

    // Check download link
    const downloadLink = screen.getAllByText('Download')[0] as HTMLAnchorElement;
    expect(downloadLink.href).toMatch(/\/task-runs\/mock-uuid-1\/logs\/merged$/);
  });

  it('keeps panels expanded when operators use their controls', async () => {
    const clipboardMock = { writeText: vi.fn() };
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: clipboardMock,
    });

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/logs/stdout')) return Promise.resolve({ ok: true, text: async () => 'stdout data' } as Response);
      if (url.includes('/diagnostics')) return Promise.resolve({ ok: true, text: async () => '{"diag":true}' } as Response);
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-08T00:00:01Z',
                stream: 'stdout',
                text: 'live log data\n',
              },
            ],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/logs/merged')) return Promise.resolve({ ok: true, text: async () => 'live log data' } as Response);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({ ok: true, json: async () => ({ summary: { status: 'completed' } }) } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          taskId: 'test-123',
          workflowId: 'test-123',
          temporalRunId: '01-run',
          namespace: 'default',
          taskRunId: 'mock-uuid-1',
          source: 'temporal',
          title: 'Mock task',
          summary: 'Mock summary',
          state: 'succeeded',
          status: 'completed',
          createdAt: '2026-03-28T00:00:00Z',
        }),
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));
    fireEvent.click(await screen.findByText('Stdout'));
    fireEvent.click(await screen.findByText('Diagnostics'));

    await waitFor(() => expect(screen.getByText(/live log data/)).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/stdout data/)).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/\{"diag":true\}/)).toBeTruthy());

    const liveDetails = screen.getByText('Live Logs').closest('details');
    const stdoutDetails = screen.getByText('Stdout').closest('details');
    const diagnosticsDetails = screen.getByText('Diagnostics').closest('details');

    fireEvent.click(screen.getAllByText('Copy')[0]!);
    fireEvent.click(screen.getAllByLabelText('Wrap lines')[1]!);
    fireEvent.click(screen.getAllByText('Copy')[2]!);

    expect(liveDetails?.hasAttribute('open')).toBe(true);
    expect(stdoutDetails?.hasAttribute('open')).toBe(true);
    expect(diagnosticsDetails?.hasAttribute('open')).toBe(true);
  });

  it('polls execution detail until taskRunId appears and then attaches observability panels', async () => {
    let detailCalls = 0;
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({ ok: true, json: async () => activeSummary } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: [
              {
                sequence: 1,
                timestamp: '2026-04-08T00:00:01Z',
                stream: 'stdout',
                text: 'attached tail\n',
              },
            ],
            truncated: false,
          }),
        } as Response);
      }
      if (url.includes('/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => 'attached tail\n' } as unknown as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      detailCalls += 1;
      return Promise.resolve({
        ok: true,
        json: async () =>
          detailCalls === 1
            ? {
                ...activeExecution,
                taskRunId: undefined,
                updatedAt: '2026-03-28T00:00:00Z',
              }
            : activeExecution,
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={fastPollPayload} />);

    await waitFor(() => {
      expect(
        screen.getByText(/Waiting for managed runtime launch to create live logs/i),
      ).toBeTruthy();
    });

    await waitFor(() => {
      expect(screen.getByText(/attached tail/)).toBeTruthy();
    });
  });

  it('shows launch-failed copy when execution ends without a managed run binding', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          ...terminalExecution,
          taskRunId: undefined,
          updatedAt: '2026-03-28T00:00:02Z',
        }),
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(
        screen.getByText(/ended before a managed runtime observability record was created/i),
      ).toBeTruthy();
    });
  });

  it('shows binding-missing copy when execution is still running without a managed run binding', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          ...activeExecution,
          taskRunId: undefined,
          updatedAt: '2026-03-28T00:00:02Z',
        }),
      } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(
        screen.getByText(/has not received its managed runtime binding yet/i),
      ).toBeTruthy();
    });
  });

  it('shows authorization-specific copy when observability summary returns 403', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: false,
          status: 403,
          text: async () => 'forbidden',
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => activeExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([url]) => String(url).includes('/observability-summary')),
      ).toBe(true);
    }, { timeout: 5000 });

    expect(
      await screen.findByText(
        /do not have permission to view observability for this run/i,
        {},
        { timeout: 5000 },
      ),
    ).toBeTruthy();
  });
});
