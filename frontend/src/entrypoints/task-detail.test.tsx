import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, act, fireEvent } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import { getSessionProjectionRefetchInterval, TaskDetailPage } from './task-detail';
import { BootPayload } from '../boot/parseBootPayload';
import { MockInstance } from 'vitest';

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
    data: { sequence: number; stream: string; text: string; timestamp?: string; kind?: string },
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
    data: { sequence: number; stream: string; text: string; timestamp?: string; kind?: string },
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

describe('Task Detail Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'task-detail',
    apiBase: '/api',
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
    window.history.pushState({}, 'Test', '/tasks/test-123?source=temporal');
    fetchSpy = vi.spyOn(window, 'fetch');
    fetchSpy.mockClear();
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
      expect(screen.getByText('Latest run')).toBeTruthy();
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

    fireEvent.click(screen.getByRole('button', { name: 'Expand step Apply patch' }));

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
    });

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
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Steps' })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Expand step Apply patch' }));

    await waitFor(() => {
      expect(screen.getByText(/waiting for managed runtime launch to create live logs/i)).toBeTruthy();
    });

    await waitFor(() => {
      expect(screen.getByText('attached after refresh')).toBeTruthy();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/task-runs/task-run-step-1/observability-summary')),
    ).toBe(true);
  });

  it('renders loading state initially', () => {
    fetchSpy.mockImplementation(() => new Promise(() => {}));
    renderWithClient(<TaskDetailPage payload={mockPayload} />);
    expect(screen.getByText(/Loading task/i)).toBeTruthy();
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
      profileId: 'profile:gemini-default',
      providerId: 'google',
      providerLabel: 'Google',
      title: 'Example task',
      summary: 'Did work',
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
      expect(screen.getByText('Google')).toBeTruthy();
      expect(screen.getByText('profile:gemini-default')).toBeTruthy();
      expect(screen.getByRole('link', { name: 'https://github.com/MoonLadderStudios/MoonMind/pull/123' })).toBeTruthy();
    });

    expect(fetchSpy).toHaveBeenCalledWith('/api/executions/test-123?source=temporal');
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
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const es = MockEventSource.instances[0]!;

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

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const es = MockEventSource.instances[0]!;

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

      await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
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

    await waitFor(() => expect(screen.getByText(/Stream ended/)).toBeTruthy());
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
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => currentExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const es = MockEventSource.instances[0]!;
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

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
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
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
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
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
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
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
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
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => activeExecution } as Response);
    });

    renderWithClient(<TaskDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(
        screen.getByText(/do not have permission to view observability for this run/i),
      ).toBeTruthy();
    });
  });
});
