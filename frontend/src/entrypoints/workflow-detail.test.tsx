import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, act, fireEvent, within } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import { EXECUTING_STATUS_PILL_TRACEABILITY } from '../utils/executionStatusPillClasses';
import {
  expandRouteTemplate,
  getSessionProjectionRefetchInterval,
  normalizeObservabilityEvent,
  WorkflowDetailEntrypoint,
  WorkflowDetailPage,
} from './workflow-detail';
import {
  taskCompareHref,
  taskEditForRerunHref,
  taskEditHref,
} from '../lib/temporalTaskEditing';
import { navigateTo } from '../lib/navigation';
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

vi.mock('../lib/navigation', () => ({
  navigateTo: vi.fn(),
}));

function lastFetchUrl(fetchSpy: MockInstance, prefix: string): string | undefined {
  return fetchSpy.mock.calls
    .map(([url]) => String(url))
    .filter((url) => url.startsWith(prefix))
    .at(-1);
}

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

describe('Workflow Detail Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'workflow-detail',
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
    page: 'workflow-detail',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        pollIntervalsMs: { detail: 1 },
        sources: {
          agentRuns: {
            observabilitySummary: '/api/agent-runs/{agentRunId}/observability-summary',
            observabilityEvents: '/api/agent-runs/{agentRunId}/observability/events',
            logsStream: '/api/agent-runs/{agentRunId}/logs/stream',
            logsStdout: '/api/agent-runs/{agentRunId}/logs/stdout',
            logsStderr: '/api/agent-runs/{agentRunId}/logs/stderr',
            logsMerged: '/api/agent-runs/{agentRunId}/logs/merged',
            diagnostics: '/api/agent-runs/{agentRunId}/diagnostics',
            artifactSession: '/api/agent-runs/{agentRunId}/artifact-sessions/{sessionId}',
            artifactSessionControl: '/api/agent-runs/{agentRunId}/artifact-sessions/{sessionId}/control',
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
        executionOrdinal: 1,
        startedAt: '2026-04-09T00:00:01Z',
        updatedAt: '2026-04-09T00:00:02Z',
        summary: 'Plan complete',
        checks: [],
        refs: { childWorkflowId: null, childRunId: null, agentRunId: null },
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
        executionOrdinal: 1,
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
          agentRunId: 'agent-run-step-1',
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
        executionOrdinal: 0,
        startedAt: null,
        updatedAt: '2026-04-09T00:00:04Z',
        summary: 'Ready to start',
        checks: [],
        refs: { childWorkflowId: null, childRunId: null, agentRunId: null },
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

  function richOutboundRemediationLink(overrides: Record<string, unknown> = {}) {
    return {
      remediationWorkflowId: 'test-remediation-rich',
      remediationRunId: '01-run',
      targetWorkflowId: 'mm:target-rich',
      targetRunId: 'run-target-rich',
      mode: 'snapshot_then_follow',
      authorityMode: 'approval_gated',
      status: 'awaiting_approval',
      activeLockScope: 'target_execution',
      activeLockHolder: 'test-remediation-rich',
      latestActionSummary: 'Proposed session interrupt',
      resolution: 'precondition_failed',
      contextArtifactRef: 'art_context_rich',
      selectedSteps: ['collect-context', 'repair-runtime'],
      currentTargetState: 'awaiting_external',
      allowedActions: ['inspect_context', 'request_approval', 'terminate_session'],
      evidenceDegraded: true,
      unavailableEvidenceClasses: ['runtime_stderr', 'provider_snapshot'],
      liveObservation: {
        status: 'active',
        label: 'Live observation active',
        sequenceCursor: 'stdout:42',
        reconnectState: 'reconnected',
        epoch: 'run-target-rich:2',
        fallbackReason: 'Durable context remains authoritative.',
      },
      lockOutcome: {
        state: 'conflict',
        holder: 'test-remediation-rich',
        releasedAt: null,
      },
      approvalState: {
        requestId: 'approval-rich',
        actionKind: 'session_interrupt',
        riskTier: 'high',
        preconditions: 'Target run is still awaiting an external session.',
        blastRadius: 'One managed runtime session.',
        decision: 'pending',
        canDecide: true,
        auditRef: 'audit-rich',
      },
      createdAt: '2026-04-22T00:00:02Z',
      updatedAt: '2026-04-22T00:00:03Z',
      ...overrides,
    };
  }

  let fetchSpy: MockInstance;

  beforeEach(() => {
    vi.restoreAllMocks();
    virtuosoPropsSpy.mockClear();
    window.history.pushState({}, 'Test', '/workflows/test-123/steps?source=temporal');
    window.sessionStorage.clear();
    window.localStorage.clear();
    fetchSpy = vi.spyOn(window, 'fetch');
    fetchSpy.mockClear();
    vi.mocked(navigateTo).mockReset();
  });

  async function openWorkflowActionsMenu() {
    fireEvent.click(await screen.findByRole('button', { name: 'Workflow actions' }));
    return screen.getByRole('menu', { name: 'Workflow actions' });
  }

  function confirmWorkflowDialog(name: string) {
    fireEvent.click(screen.getByRole('button', { name }));
  }

  function typeWorkflowConfirmation(text: string) {
    fireEvent.change(screen.getByLabelText(`Type ${text} to confirm`), {
      target: { value: text },
    });
  }

  function mockWorkflowDetailSubrouteFetch() {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'MM-801 routed workflow',
      summary: 'Focused route summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
      relatedRuns: [
        {
          workflowId: 'test-456',
          runId: '03-run',
          relationship: 'rerun',
          status: 'failed',
          href: '/workflows/test-456/runs?source=temporal',
        },
      ],
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
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifactId: 'report-001',
                contentType: 'text/markdown',
                sizeBytes: 128,
                status: 'complete',
                metadata: { title: 'Final report' },
                links: [{ linkType: 'report.primary' }],
                defaultReadRef: { artifactId: 'report-001' },
              },
            ],
          }),
        } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifacts: [
              {
                artifactId: 'report-001',
                contentType: 'text/markdown',
                sizeBytes: 128,
                status: 'complete',
                metadata: { title: 'Final report' },
                links: [{ linkType: 'report.primary' }],
                defaultReadRef: { artifactId: 'report-001' },
              },
              {
                artifactId: 'evidence-001',
                contentType: 'application/json',
                sizeBytes: 256,
                status: 'complete',
                metadata: { filename: 'evidence.json' },
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
  }

  function mockWorkflowWorkspaceFetches() {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'MM-997 selected workflow',
      summary: 'Workspace shell selected detail',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
      relatedRuns: [],
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/executions?')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                workflowId: 'test-123',
                taskId: 'test-123',
                source: 'temporal',
                title: 'MM-997 selected workflow',
                status: 'running',
                state: 'executing',
                rawState: 'executing',
                createdAt: '2026-04-09T00:00:00Z',
              },
              {
                workflowId: 'test-456',
                taskId: 'test-456',
                source: 'temporal',
                title: 'Another workflow',
                status: 'completed',
                state: 'completed',
                rawState: 'completed',
                createdAt: '2026-04-08T00:00:00Z',
              },
            ],
          }),
        } as Response);
      }
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
  }

  function mockDesktopViewport(matches = true) {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  }

  it('MM-997 renders desktop workflow detail routes inside the workspace shell by default', async () => {
    window.history.pushState({}, 'Workspace Detail Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(sidebar).toBeTruthy();
    expect(screen.getByRole('main', { name: 'Workflow detail' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    const active = await within(sidebar).findByRole('link', { name: /MM-997 selected workflow/i });
    expect(active.getAttribute('aria-current')).toBe('page');
    expect((await within(sidebar).findByRole('link', { name: /Another workflow/i })).getAttribute('href')).toBe(
      '/workflows/test-456?source=temporal',
    );
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).toBe('/api/executions?source=temporal&pageSize=25');
  });

  it('MM-997 translates workspace sidebar limit state to the executions API page size', async () => {
    window.history.pushState(
      {},
      'Workspace Query Test',
      '/workflows/test-123?source=temporal&limit=10&nextPageToken=page-2&selectedWorkflowId=test-123',
    );
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    expect(await screen.findByRole('complementary', { name: 'Workflow navigation' })).toBeTruthy();
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).toBe(
      '/api/executions?source=temporal&nextPageToken=page-2&pageSize=10',
    );
  });

  it('MM-997 keeps workflow detail standalone when the workflow list is disabled', async () => {
    window.history.pushState({}, 'Workspace List Disabled Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(
      <WorkflowDetailEntrypoint
        payload={{
          ...stepsPayload,
          initialData: {
            dashboardConfig: {
              pollIntervalsMs: { detail: 1 },
              features: {
                temporalDashboard: {
                  listEnabled: false,
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).toBeUndefined();
  });

  it.each([
    ['/workflows/test-123/steps?source=temporal', 'Workflow Steps'],
    ['/workflows/test-123/artifacts?source=temporal', 'Workflow Artifacts'],
    ['/workflows/test-123/runs?source=temporal', 'Execution History'],
  ])('MM-997 keeps %s inside the desktop workspace shell', async (path, heading) => {
    window.history.pushState({}, 'Workspace Subroute Test', path);
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    expect(await screen.findByRole('complementary', { name: 'Workflow navigation' })).toBeTruthy();
    expect(await screen.findByRole('heading', { name: heading })).toBeTruthy();
  });

  it('MM-997 disables only the desktop workspace shell when the runtime flag is false', async () => {
    window.history.pushState({}, 'Workspace Disabled Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(
      <WorkflowDetailEntrypoint
        payload={{
          ...stepsPayload,
          initialData: {
            dashboardConfig: {
              pollIntervalsMs: { detail: 1 },
              features: {
                temporalDashboard: {
                  workspaceShellEnabled: false,
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();
  });

  it('MM-997 keeps mobile detail navigation standalone even when the shell flag is enabled', async () => {
    window.history.pushState({}, 'Workspace Mobile Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(false);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();
  });

  it('MM-801 renders Overview as a concise summary with route preview cards', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
      expect(screen.getByText('Live updates enabled. Polling every 0s')).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Workflow Preview' })).toBeTruthy();
      expect(screen.getByText('Focused route summary')).toBeTruthy();
      expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBe('page');
      expect(screen.getByRole('link', { name: 'Steps' }).getAttribute('href')).toBe('/workflows/test-123/steps?source=temporal');
      expect(screen.getByText('3 steps')).toBeTruthy();
      expect(screen.queryByRole('heading', { name: 'Workflow Steps' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Workflow Artifacts' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Execution History' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Run Comparison' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Timeline' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Report' })).toBeNull();
    });
  });

  it('renders recovery evidence from the failed step execution detail payload', async () => {
    window.history.pushState({}, 'Recovery Evidence Test', '/workflows/test-123/steps?source=temporal');
    const failedStepsSnapshot = {
      ...latestStepsSnapshot,
      steps: latestStepsSnapshot.steps.map((step) =>
        step.logicalStepId === 'apply'
          ? { ...step, status: 'failed', executionOrdinal: 2 }
          : step,
      ),
    };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Recovery task',
      summary: 'Needs recovery',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      resume: {
        available: true,
        failedStepId: 'apply',
      },
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps/apply/step-executions/2')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            logicalStepId: 'apply',
            executionOrdinal: 2,
            recoveryEligibility: {
              eligible: true,
              defaultAction: 'resume_from_checkpoint',
              requiredBoundary: 'before_execution',
              checkpointRef: 'artifact://checkpoint/before',
              sourceWorkflowId: 'wf-source',
              sourceRunId: 'run-source',
              operatorGuidance: 'resume',
              evidence: [
                {
                  category: 'checkpoint',
                  status: 'available',
                  artifactRef: 'artifact://checkpoint/before',
                  boundary: 'before_execution',
                },
              ],
            },
          }),
        } as Response);
      }
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({
          ok: true,
          json: async () => failedStepsSnapshot,
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Recovery evidence' })).toBeTruthy();
    expect(screen.getByText(/Resume from checkpoint is the default recovery action/)).toBeTruthy();
    expect(screen.getByText('artifact://checkpoint/before')).toBeTruthy();
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/executions/test-123/steps/apply/step-executions/2?source=temporal',
      { credentials: 'include' },
    );
  });

  it('MM-801 renders Steps as the focused step ledger route', async () => {
    window.history.pushState({}, 'Steps Test', '/workflows/test-123/steps?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: 'Workflow Steps' }).length).toBeGreaterThan(0);
      expect(screen.queryByRole('heading', { name: 'Step DAG' })).toBeNull();
      expect(screen.getAllByText('Plan work').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Apply patch').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Verify tests').length).toBeGreaterThan(0);
      expect(screen.queryByLabelText('start to plan')).toBeNull();
      expect(screen.queryByLabelText('plan to apply')).toBeNull();
      expect(screen.queryByLabelText('apply to verify')).toBeNull();
      expect(screen.queryByLabelText('Step dependency edges')).toBeNull();
      expect(screen.getByRole('link', { name: 'Steps' }).getAttribute('aria-current')).toBe('page');
      expect(screen.getByRole('heading', { name: 'Timeline' })).toBeTruthy();
      expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Workflow Artifacts' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Execution History' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Run Comparison' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Report' })).toBeNull();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Show details for Apply patch' }));
    expect(screen.queryByText('Depends on: plan')).toBeNull();
    expect(screen.getAllByText((_, element) => element?.textContent === 'Prior step evidence: plan').length).toBeGreaterThan(0);
  });

  it('MM-842 renders empty steps without a separate Step DAG panel', async () => {
    window.history.pushState({}, 'Empty Steps Test', '/workflows/test-123/steps?source=temporal');
    const emptyStepsSnapshot = { ...latestStepsSnapshot, steps: [] };
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Empty step ledger',
      summary: 'No steps recorded yet',
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
          json: async () => emptyStepsSnapshot,
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
      expect(screen.getByText('0 steps')).toBeTruthy();
      expect(screen.queryByRole('heading', { name: 'Step DAG' })).toBeNull();
      expect(screen.queryByText('No steps in the ledger yet.')).toBeNull();
      expect(screen.queryByLabelText('start to none')).toBeNull();
      expect(screen.queryByLabelText('Step dependency edges')).toBeNull();
    });
  });

  it('MM-815 surfaces latest evidence refs and preserved provenance markers in the default step row', async () => {
    window.history.pushState({}, 'Steps Test', '/workflows/test-123/steps?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Resumed task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };
    const resumedSnapshot = {
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
          executionOrdinal: 1,
          startedAt: '2026-04-08T00:00:01Z',
          updatedAt: '2026-04-08T00:00:02Z',
          summary: 'Plan complete',
          checks: [],
          refs: { childWorkflowId: null, childRunId: null, agentRunId: null },
          artifacts: {
            outputSummary: null,
            outputPrimary: 'art-plan-output',
            runtimeStdout: null,
            runtimeStderr: null,
            runtimeMergedLogs: null,
            runtimeDiagnostics: null,
            providerSnapshot: null,
            stepExecutionManifestRef: null,
            stepExecutionManifestRefs: ['art-plan-manifest'],
          },
          stateCheckpointRef: 'art-plan-checkpoint',
          preservedFrom: {
            workflowId: 'test-123',
            runId: '01-run',
            logicalStepId: 'plan',
            executionOrdinal: 1,
          },
          lastError: null,
        },
        {
          logicalStepId: 'apply',
          order: 2,
          title: 'Apply patch',
          tool: { type: 'agent_runtime', name: 'codex_cli', version: '1' },
          dependsOn: ['plan'],
          status: 'failed',
          waitingReason: null,
          attentionRequired: false,
          executionOrdinal: 2,
          startedAt: '2026-04-09T00:00:03Z',
          updatedAt: '2026-04-09T00:00:04Z',
          summary: 'Applying repository changes',
          checks: [
            {
              kind: 'merge_gate',
              status: 'failed',
              summary: 'Gate failed',
              retryCount: 1,
              artifactRef: 'art-gate-verdict',
            },
          ],
          refs: { childWorkflowId: null, childRunId: null, agentRunId: null },
          artifacts: {
            outputSummary: 'art-apply-summary',
            outputPrimary: 'art-apply-output',
            runtimeStdout: null,
            runtimeStderr: null,
            runtimeMergedLogs: null,
            runtimeDiagnostics: 'art-apply-diagnostics',
            providerSnapshot: null,
          },
          lastError: null,
        },
      ],
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => resumedSnapshot } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    // Default (collapsed) rows must surface latest evidence refs and the
    // preserved-provenance marker without expanding full attempt history.
    await waitFor(() => {
      expect(screen.getAllByText('Apply patch').length).toBeGreaterThan(0);
    });

    // Preserved provenance marker on the resumed row (and not yet the expanded text).
    expect(screen.getAllByText('Preserved').length).toBeGreaterThan(0);
    expect(screen.queryByText(/Preserved from source run/)).toBeNull();

    // Latest attempt count is surfaced on the re-run row.
    expect(screen.getByText('Execution 2')).toBeTruthy();

    // Latest evidence refs are surfaced ref-only in the collapsed rows.
    expect(screen.getAllByLabelText('Latest evidence refs').length).toBeGreaterThan(0);
    expect(screen.getByText('art-plan-output')).toBeTruthy();
    expect(screen.getByText('art-apply-output')).toBeTruthy();
    expect(screen.getByText('art-apply-diagnostics')).toBeTruthy();
    expect(screen.getByText('art-gate-verdict')).toBeTruthy();
    // Step-execution manifest and recovery checkpoint refs are surfaced as the
    // only durable latest evidence for running/recovered rows.
    expect(screen.getByText('art-plan-manifest')).toBeTruthy();
    expect(screen.getByText('art-plan-checkpoint')).toBeTruthy();
  });

  it('MM-831 renders expanded Step Execution history from the step-executions list endpoint', async () => {
    window.history.pushState({}, 'Steps Test', '/workflows/test-123/steps?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'History task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };
    const stepsSnapshot = {
      workflowId: 'test-123',
      runId: '02-run',
      runScope: 'latest',
      steps: [
        {
          logicalStepId: 'apply',
          order: 1,
          title: 'Apply patch',
          tool: { type: 'agent_runtime', name: 'codex_cli', version: '1' },
          dependsOn: [],
          status: 'succeeded',
          waitingReason: null,
          attentionRequired: false,
          executionOrdinal: 2,
          startedAt: '2026-04-09T00:00:03Z',
          updatedAt: '2026-04-09T00:00:04Z',
          summary: 'Applied repository changes',
          checks: [],
          refs: { childWorkflowId: null, childRunId: null, agentRunId: null },
          artifacts: {
            outputSummary: null,
            outputPrimary: 'art-apply-output',
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
    const stepExecutionsResponse = {
      workflowId: 'test-123',
      runId: '02-run',
      runScope: 'latest',
      logicalStepId: 'apply',
      stepExecutions: [
        {
          manifestArtifactRef: 'art-exec-1-manifest',
          stepExecutionId: 'test-123:02-run:apply:1',
          workflowId: 'test-123',
          runId: '02-run',
          logicalStepId: 'apply',
          executionOrdinal: 1,
          sourceExecutionOrdinal: null,
          lineage: null,
          reason: 'initial_execution',
          status: 'failed',
          terminalDisposition: 'retryable',
          startedAt: '2026-04-09T00:00:01Z',
          updatedAt: '2026-04-09T00:00:02Z',
          summary: 'First attempt failed the gate',
          runtimeChildRefs: { childWorkflowId: 'child-wf-apply-1' },
          workspacePolicy: 'workspace_required',
          gitDisposition: 'candidate',
          qualityGateVerdict: 'ADDITIONAL_WORK_NEEDED',
          manifestRefs: { manifestArtifactRef: 'art-exec-1-manifest' },
          outputRefs: { summary: 'art-exec-1-output', diff: 'art-exec-1-diff' },
          stepEvidence: {
            logicalStepId: 'apply',
            executionOrdinal: 1,
            checkpointRefsByBoundary: {},
            contextBundleRef: {
              category: 'context',
              status: 'available',
              artifactRef: 'art-exec-1-context',
            },
            gateSummary: { verdict: 'ADDITIONAL_WORK_NEEDED', artifactRef: 'art-exec-1-gate' },
            terminalDisposition: 'retryable',
            sideEffectSummary: { status: 'skipped', artifactRefs: {} },
            diagnosticRefs: [
              {
                kind: 'environment',
                status: 'available',
                diagnosticsRef: 'art-exec-1-diag',
                reasonCode: 'sidecar_unhealthy',
                summary: 'Sidecar unhealthy',
              },
            ],
          },
          recoveryEligibility: null,
        },
        {
          manifestArtifactRef: 'art-exec-2-manifest',
          stepExecutionId: 'test-123:02-run:apply:2',
          workflowId: 'test-123',
          runId: '02-run',
          logicalStepId: 'apply',
          executionOrdinal: 2,
          sourceExecutionOrdinal: 1,
          lineage: {
            sourceWorkflowId: 'wf-source',
            sourceRunId: 'run-source',
            sourceLogicalStepId: 'apply',
            sourceExecutionOrdinal: 1,
            relationship: 'recovered_from',
          },
          reason: 'dependency_invalidated',
          status: 'succeeded',
          terminalDisposition: 'accepted',
          startedAt: '2026-04-09T00:00:03Z',
          updatedAt: '2026-04-09T00:00:04Z',
          summary: 'Re-ran after the upstream contract changed',
          runtimeChildRefs: { childWorkflowId: 'child-wf-apply-2' },
          workspacePolicy: 'workspace_required',
          gitDisposition: 'accepted',
          qualityGateVerdict: 'FULLY_IMPLEMENTED',
          manifestRefs: { manifestArtifactRef: 'art-exec-2-manifest' },
          outputRefs: { summary: 'art-exec-2-output', diff: 'art-exec-2-diff' },
          stepEvidence: {
            logicalStepId: 'apply',
            executionOrdinal: 2,
            checkpointRefsByBoundary: {},
            contextBundleRef: {
              category: 'context',
              status: 'available',
              artifactRef: 'art-exec-2-context',
            },
            gateSummary: { verdict: 'FULLY_IMPLEMENTED', artifactRef: 'art-exec-2-gate' },
            terminalDisposition: 'accepted',
            sideEffectSummary: {
              status: 'available',
              artifactRefs: { publish: 'art-exec-2-sideeffect' },
            },
            diagnosticRefs: [],
          },
          recoveryEligibility: null,
        },
      ],
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps/apply/step-executions')) {
        return Promise.resolve({ ok: true, json: async () => stepExecutionsResponse } as Response);
      }
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => stepsSnapshot } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    // History is not requested or rendered until the row is expanded.
    await waitFor(() => {
      expect(screen.getAllByText('Apply patch').length).toBeGreaterThan(0);
    });
    expect(screen.queryByLabelText('Step Execution history')).toBeNull();

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    // The expanded surface consumes the step-executions LIST endpoint.
    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([input]) =>
          String(input).includes(
            '/api/executions/test-123/steps/apply/step-executions?source=temporal',
          ),
        ),
      ).toBe(true);
    });

    const history = await screen.findByLabelText('Step Execution history');
    expect(screen.getByRole('heading', { name: 'Step Execution history' })).toBeTruthy();
    expect(screen.getByText('2 step executions')).toBeTruthy();

    // Newest execution renders first.
    const ordinals = Array.from(
      (history as HTMLElement).querySelectorAll('.step-execution-pill'),
    ).map((node) => node.textContent);
    expect(ordinals).toEqual(['Execution 2', 'Execution 1']);

    // Downstream invalidation status is surfaced in the compact history rows.
    expect(within(history).getByText('Downstream invalidation')).toBeTruthy();

    // Full enumerated evidence field set is surfaced as refs / typed diagnostics.
    expect(within(history).getByText('Lineage')).toBeTruthy();
    expect(within(history).getByText('Source attempt')).toBeTruthy();
    expect(within(history).getAllByText('Terminal disposition').length).toBe(2);
    expect(within(history).getAllByText('Workspace policy').length).toBe(2);
    expect(within(history).getAllByText('Git disposition').length).toBe(2);
    expect(within(history).getAllByText('Gate verdict').length).toBe(2);
    expect(within(history).getAllByText('Context bundle').length).toBe(2);
    expect(within(history).getAllByText('Runtime child refs').length).toBe(2);
    expect(within(history).getByText('Diagnostics refs')).toBeTruthy();
    expect(within(history).getAllByText('Side effects').length).toBe(2);

    // Ref-only values, never inlined bodies.
    expect(within(history).getByText('art-exec-2-context')).toBeTruthy();
    expect(within(history).getByText('art-exec-1-context')).toBeTruthy();
    expect(within(history).getByText('art-exec-2-diff')).toBeTruthy();
    expect(within(history).getByText('art-exec-1-diag')).toBeTruthy();
    expect(within(history).getByText('art-exec-2-sideeffect')).toBeTruthy();
    expect(within(history).getByText('child-wf-apply-2')).toBeTruthy();
    expect(within(history).getByText('wf-source')).toBeTruthy();
  });

  it('MM-801 renders Artifacts as the focused report and artifact route', async () => {
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: 'Workflow Artifacts' }).length).toBeGreaterThan(0);
      expect(screen.getByRole('heading', { name: 'Report' })).toBeTruthy();
      expect(screen.getAllByText('Final report').length).toBeGreaterThan(0);
      expect(screen.getByText('evidence.json')).toBeTruthy();
      expect(screen.getByRole('link', { name: 'Artifacts' }).getAttribute('aria-current')).toBe('page');
      expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Step DAG' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Workflow Steps' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Timeline' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Execution History' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Run Comparison' })).toBeNull();
    });
  });

  it('MM-801 renders Runs as the focused execution history and comparison route', async () => {
    window.history.pushState({}, 'Runs Test', '/workflows/test-123/runs?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: 'Execution History' }).length).toBeGreaterThan(0);
      expect(screen.getByRole('heading', { name: 'Run Comparison' })).toBeTruthy();
      expect(screen.getAllByText('test-456').length).toBeGreaterThan(0);
      expect(screen.getByRole('link', { name: 'Runs' }).getAttribute('aria-current')).toBe('page');
      expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Workflow Steps' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Workflow Artifacts' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Timeline' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Report' })).toBeNull();
    });
  });

  it('MM-957 keeps raw Temporal facts out of the default overview while preserving summary and evidence sections', async () => {
    window.history.pushState({}, 'Overview Debug IA Test', '/workflows/test-123?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      // Summary and evidence preview sections still render on the default overview.
      expect(screen.getByRole('heading', { name: 'Summary' })).toBeTruthy();
      expect(screen.getByText('Focused route summary')).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Workflow Preview' })).toBeTruthy();
      expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBe('page');
    });

    // The raw Temporal FactGroup no longer competes with outcome/evidence content.
    expect(screen.queryByRole('heading', { name: 'Temporal' })).toBeNull();
    for (const label of ['Temporal Status', 'Workflow State', 'Workflow Type', 'Entry', 'Source']) {
      expect(screen.queryByText(new RegExp(`^${label}:?$`))).toBeNull();
    }
    // The hero no longer leads with Temporal metadata, but the compact workflow ID stays accessible.
    expect(screen.queryByText('MoonMind.UserWorkflow')).toBeNull();
    expect(screen.getAllByText('test-123').length).toBeGreaterThan(0);
  });

  it('MM-957 exposes the moved Temporal fields on the keyboard-accessible Debug subroute', async () => {
    window.history.pushState({}, 'Debug Route Test', '/workflows/test-123/debug?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Debug' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Temporal' })).toBeTruthy();
    });

    // All moved Temporal/runtime identifiers are exposed in the Debug view.
    for (const label of [
      'Temporal Status',
      'Workflow State',
      'Source',
      'Workflow Type',
      'Entry',
      'Current Run ID',
      'Workflow ID',
    ]) {
      expect(screen.getByText(new RegExp(`^${label}:?$`))).toBeTruthy();
    }
    expect(screen.getAllByText('MoonMind.UserWorkflow').length).toBeGreaterThan(0);

    // Deep-linking to the subroute selects the Debug tab, and tab navigation uses
    // focusable anchor links (keyboard accessible by default).
    const debugLink = screen.getByRole('link', { name: 'Debug' });
    expect(debugLink.getAttribute('aria-current')).toBe('page');
    expect(debugLink.tagName).toBe('A');
    expect(debugLink.getAttribute('href')).toBe('/workflows/test-123/debug?source=temporal');
    // Overview is reachable from the Debug subroute (deep links round-trip).
    expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('href')).toBe('/workflows/test-123?source=temporal');

    // Raw Temporal facts are scoped to Debug only — not the Overview preview cards.
    expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
  });

  it('MM-964 hides the Debug tab when the debug-visibility preference is turned off and remembers it', async () => {
    window.history.pushState({}, 'Debug Pref Test', '/workflows/test-123?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    const view = renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    // Debug tab is visible by default.
    expect(await screen.findByRole('link', { name: 'Debug' })).toBeTruthy();

    const toggle = screen.getByLabelText('Show debug details') as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    fireEvent.click(toggle);

    // The Debug tab disappears and the preference is persisted.
    expect(screen.queryByRole('link', { name: 'Debug' })).toBeNull();
    expect(window.localStorage.getItem('moonmind.dashboard.preferences')).toContain(
      '"debugFieldsVisible":false',
    );

    // Simulate a reload: a fresh mount keeps the Debug tab hidden.
    view.unmount();
    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);
    await screen.findByRole('link', { name: 'Overview' });
    expect(screen.queryByRole('link', { name: 'Debug' })).toBeNull();
    expect(
      (screen.getByLabelText('Show debug details') as HTMLInputElement).checked,
    ).toBe(false);
  });

  it('returns null for route templates with missing parameters', () => {
    expect(
      expandRouteTemplate('/api/agent-runs/{agentRunId}/artifact-sessions/{sessionId}', {
        agentRunId: 'agent-run-1',
        sessionId: null,
      }),
    ).toBeNull();
    expect(
      expandRouteTemplate('/api/agent-runs/{agentRunId}/artifact-sessions/{sessionId}', {
        agentRunId: 'agent-run-1',
      }),
    ).toBeNull();
  });

  it('renders a Steps section above Timeline and Artifacts and loads steps before execution-wide artifacts', async () => {
    window.history.pushState({}, 'Steps Test', '/workflows/test-123/steps?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
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

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
      expect(screen.getAllByText('Plan work').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Apply patch').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Verify tests').length).toBeGreaterThan(0);
      expect(screen.queryByRole('heading', { name: 'Step DAG' })).toBeNull();
      expect(screen.queryByText('Depends on: plan')).toBeNull();
      expect(screen.getAllByText('Prior step evidence: plan').length).toBeGreaterThan(0);
      expect(screen.queryByLabelText('plan to apply')).toBeNull();
      expect(screen.queryByLabelText('apply to verify')).toBeNull();
      expect(screen.getAllByText('02-run').length).toBeGreaterThan(0);
    });

    expect(screen.queryByText('Depends on: plan')).toBeNull();
    expect(screen.getAllByText((_, element) => element?.textContent === 'Prior step evidence: plan').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: 'Show details for Apply patch' }));
    expect(screen.queryByText('Depends on: plan')).toBeNull();
    expect(screen.getAllByText((_, element) => element?.textContent === 'Prior step evidence: plan').length).toBeGreaterThan(0);

    const stepsHeading = screen.getByRole('heading', { name: 'Workflow Steps' });
    const timelineHeading = screen.getByRole('heading', { name: 'Timeline' });
    expect(screen.queryByRole('heading', { name: 'Workflow Artifacts' })).toBeNull();

    const positions: [number] = [
      stepsHeading.compareDocumentPosition(timelineHeading),
    ];
    expect(positions[0] & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

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

  it('renders the required workflow execution identity and section labels', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      temporalRunId: '02-run',
      runId: '02-run',
      namespace: 'default',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Workflow detail labels',
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

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      // MM-957: raw Temporal fact labels are no longer surfaced on the default overview.
      for (const label of [
        'Workflow ID',
        'Current Run ID',
        'Workflow Type',
        'Workflow State',
      ]) {
        expect(screen.queryByText(new RegExp(`^${label}:?$`))).toBeNull();
      }
      // The compact workflow ID stays accessible in the hero as secondary metadata.
      expect(screen.getAllByText('test-123').length).toBeGreaterThan(0);
      expect(screen.getByRole('button', { name: 'Show Workflow Inputs' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Workflow Preview' })).toBeTruthy();
      expect(screen.getByRole('link', { name: 'Debug' })).toBeTruthy();
      expect(screen.queryByRole('heading', { name: 'Workflow Steps' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Workflow Artifacts' })).toBeNull();
      expect(screen.queryByText(/^Task ID:?$/)).toBeNull();
      expect(screen.queryByText(/^Task Detail:?$/)).toBeNull();
      expect(screen.queryByText(/^Step Attempt:?$/)).toBeNull();
    });
  });

  it('groups workflow artifacts and folds step and intervention events into the audit timeline', async () => {
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Artifact browser task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      startedAt: '2026-04-09T00:00:01Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
      interventionAudit: [
        {
          action: 'send_message',
          transport: 'temporal_update',
          summary: 'Operator sent guidance.',
          createdAt: '2026-04-09T00:00:05Z',
        },
      ],
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
          json: async () => ({
            artifacts: [
              {
                artifactId: 'art-log',
                contentType: 'text/plain',
                sizeBytes: 120,
                status: 'complete',
                metadata: { filename: 'runtime.log' },
              },
              {
                artifactId: 'art-patch',
                contentType: 'text/x-diff',
                sizeBytes: 80,
                status: 'complete',
                metadata: { filename: 'fix.patch' },
              },
              {
                artifactId: 'art-report-by-type',
                contentType: 'text/plain',
                sizeBytes: 256,
                status: 'complete',
                metadata: { filename: 'output.txt', artifact_type: 'report.primary' },
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

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Artifacts' })).toBeTruthy();
      expect(screen.getByText(/Artifact Browser/)).toBeTruthy();
      expect(screen.getByText('runtime.log')).toBeTruthy();
      expect(screen.getByText('fix.patch')).toBeTruthy();
      expect(screen.getByText('output.txt').closest('tr')?.textContent).toContain('reports');
    });
  });

  it('renders compact proposal delivery diagnostics from execution detail outcomes', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Proposal detail task',
      summary: 'Execution summary',
      status: 'running',
      state: 'proposals',
      rawState: 'proposals',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
      proposalSummary: {
        requested: true,
        generatedCount: 3,
        submittedCount: 3,
        deliveredCount: 1,
        externalLinks: [],
        dedupUpdates: [
          {
            provider: 'github',
            externalKey: '42',
            created: false,
            duplicateSource: 'existing-open-issue',
          },
        ],
        validationErrors: [
          { code: 'proposal_validation_error', message: 'proposal skipped: [REDACTED]' },
        ],
        deliveryFailures: [
          { provider: 'jira', code: 'delivery_failed', message: 'delivery failed: [REDACTED]' },
        ],
      },
      proposalOutcomes: [
        {
          provider: 'jira',
          externalKey: 'MM-901',
          externalUrl: 'https://jira.example/browse/MM-901',
          deliveryStatus: 'delivered',
          deliveredAt: '2026-04-09T00:01:00Z',
          lastSyncedAt: '2026-04-09T00:02:00Z',
          taskPreview: {
            repository: 'MoonLadderStudios/MoonMind',
            runtimeMode: 'codex_cli',
            publishMode: 'pr',
            priority: 4,
            maxAttempts: 2,
            taskSkills: ['fix-ci'],
            presetProvenance: 'jira-preset',
          },
          promotionResult: {
            promotedExecutionId: 'mm-promoted-1',
            promotedExecutionUrl: '/workflows/mm-promoted-1?source=temporal',
          },
        },
        {
          provider: 'github',
          externalKey: '42',
          deliveryStatus: 'updated',
          created: false,
          duplicateSource: 'existing-open-issue',
        },
        {
          provider: 'jira',
          externalKey: 'MM-902',
          deliveryStatus: 'failed',
          message: 'delivery failed: [REDACTED]',
        },
      ],
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      const summaryCard = (label: string) => screen.getByText((_, element) => element?.textContent === `${label}:`).closest('.card');
      expect(screen.getByRole('heading', { name: 'Proposal Outcomes' })).toBeTruthy();
      expect(summaryCard('Delivered')?.textContent).toContain('1');
      expect(summaryCard('Updated')?.textContent).toContain('1');
      expect(summaryCard('Failed')?.textContent).toContain('2');
      expect(screen.getByText('jira: MM-901')).toBeTruthy();
      expect(screen.getAllByText('Delivery Status').length).toBeGreaterThan(0);
      expect(screen.getByText('delivered')).toBeTruthy();
      expect(screen.getByText('MoonLadderStudios/MoonMind')).toBeTruthy();
      expect(screen.getByText('Codex CLI')).toBeTruthy();
      expect(screen.getByText('fix-ci')).toBeTruthy();
      expect(screen.getByText('jira-preset')).toBeTruthy();
      expect(screen.getByText('existing-open-issue')).toBeTruthy();
      expect(screen.getByText('mm-promoted-1')).toBeTruthy();
      expect(screen.getAllByText('delivery failed: [REDACTED]').length).toBeGreaterThan(0);
      expect(screen.queryByText(/ghp_secret/i)).toBeNull();
    });
  });

  it('does not poll terminal workflow detail surfaces after the initial load', async () => {
    const terminalExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Terminal task',
      summary: 'Execution finished',
      status: 'completed',
      state: 'succeeded',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      closeStatus: 'COMPLETED',
      closedAt: '2026-04-09T00:00:05Z',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:05Z',
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
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => terminalExecution,
      } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    const detailSurfaceCalls = () => fetchSpy.mock.calls
      .map(([input]) => String(input))
      .filter((url) => (
        url.includes('/api/executions/test-123?source=temporal') ||
        url.includes('/api/executions/test-123/steps') ||
        url.includes('/artifacts')
      ));

    await waitFor(() => {
      const urls = detailSurfaceCalls();
      expect(urls.some((url) => url.includes('/api/executions/test-123?source=temporal'))).toBe(true);
      expect(urls.some((url) => url.includes('/api/executions/test-123/steps'))).toBe(true);
      expect(urls.filter((url) => url.includes('/artifacts')).length).toBeGreaterThanOrEqual(2);
    });

    const callsAfterInitialLoad = detailSurfaceCalls();
    await new Promise((resolve) => setTimeout(resolve, 25));

    expect(detailSurfaceCalls()).toEqual(callsAfterInitialLoad);
  });

  it('displays original slash instructions and missing runtime command metadata state', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Legacy slash task',
      summary: 'Execution summary',
      taskInstructions: '/future-command\nUse provider behavior.',
      status: 'completed',
      state: 'completed',
      rawState: 'completed',
      temporalStatus: 'completed',
      targetRuntime: 'codex_cli',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => latestStepsSnapshot } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Show Workflow Inputs' }));

    expect(
      screen.getAllByText((_, element) =>
        element?.textContent === '/future-command\nUse provider behavior.',
      ).length,
    ).toBeGreaterThan(0);
    expect(await screen.findByText('Runtime Command')).toBeTruthy();
    expect(screen.getByText('Historical runtime command metadata is not available.')).toBeTruthy();
  });

  it('displays slash command interpretation metadata from the execution snapshot', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Slash task',
      summary: 'Execution summary',
      taskInstructions: '/review\nCheck the branch.',
      status: 'completed',
      state: 'completed',
      rawState: 'completed',
      temporalStatus: 'completed',
      targetRuntime: 'codex_cli',
      inputParameters: {
        task: {
          runtimeCommand: {
            command: 'review',
            rawCommand: '/review',
            targetRuntime: 'codex_cli',
            recognitionMode: 'hinted_runtime_passthrough',
            renderMode: 'prompt_prefix',
            detectionStatus: 'detected',
            hintStatus: 'hinted',
            hintCatalogVersion: '2026-05-13',
          },
        },
      },
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => latestStepsSnapshot } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    expect(await screen.findByText('Runtime Command')).toBeTruthy();
    expect(screen.getByText('/review')).toBeTruthy();
    expect(screen.getAllByText('Codex CLI').length).toBeGreaterThan(0);
    expect(screen.getByText('prompt prefix')).toBeTruthy();
    expect(screen.getByText('detected')).toBeTruthy();
    expect(screen.getAllByText('2026-05-13').length).toBeGreaterThanOrEqual(1);
  });

  it('displays legacy snake_case slash command metadata from historical execution snapshots', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Legacy slash task',
      summary: 'Execution summary',
      taskInstructions: '/review\nCheck the branch.',
      status: 'completed',
      state: 'completed',
      rawState: 'completed',
      temporalStatus: 'completed',
      targetRuntime: 'codex_cli',
      input_parameters: {
        task: {
          runtime_command: {
            command: 'review',
            rawCommand: '/review',
            targetRuntime: 'codex_cli',
            render_mode: 'prompt_prefix',
            status: 'rendered',
          },
        },
      },
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => latestStepsSnapshot } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    expect(await screen.findByText('Runtime Command')).toBeTruthy();
    expect(screen.getByText('/review')).toBeTruthy();
    expect(screen.getByText('prompt prefix')).toBeTruthy();
    expect(screen.queryByText('Historical runtime command metadata is not available.')).toBeNull();
  });


  it('renders planning detail pills with the shared shimmer selector contract and keeps dependency pills inactive when appropriate', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Planning detail task',
      summary: 'Execution summary',
      status: 'running',
      state: 'planning',
      rawState: 'planning',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await screen.findByText('Planning detail task');
    const toolbarStatus = document.querySelector<HTMLElement>('.toolbar-identity-row [data-effect="shimmer-sweep"]');
    expect(toolbarStatus?.dataset.state).toBe('planning');
    expect(toolbarStatus?.dataset.effect).toBe('shimmer-sweep');
    expect(toolbarStatus?.className).toContain('is-planning');
    expect(toolbarStatus?.className).toContain('status-running');
    expect(toolbarStatus?.dataset.shimmerLabel).toBe('planning');
    expect(toolbarStatus?.getAttribute('aria-label')).toBe('planning');
    expect(toolbarStatus?.querySelector('.status-letter-wave')?.getAttribute('aria-hidden')).toBe('true');
    const glyphs = Array.from(toolbarStatus?.querySelectorAll<HTMLElement>('.status-letter-wave__glyph') || []);
    expect(glyphs).toHaveLength('planning'.length);
    expect(glyphs.map((glyph) => glyph.textContent).join('')).toBe('planning');
    expect(toolbarStatus?.textContent).toBe('planning');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-489');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-490');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-491');

    const waitingPill = await screen.findByText('AWAITING DEP');
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
      workflowType: 'MoonMind.UserWorkflow',
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
          executionOrdinal: 1,
          startedAt: '2026-04-09T00:00:01Z',
          updatedAt: '2026-04-09T00:00:04Z',
          summary: 'Workload failed',
          checks: [],
          refs: { childWorkflowId: null, childRunId: null, agentRunId: 'agent-run-workload' },
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
            agentRunId: 'agent-run-workload',
            stepId: 'workload-step',
            executionOrdinal: 1,
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
      if (url.includes('/agent-runs/agent-run-workload/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              runId: 'agent-run-workload',
              status: 'failed',
              supportsLiveStreaming: false,
              liveStreamStatus: 'ended',
            },
          }),
        } as Response);
      }
      if (url.includes('/agent-runs/agent-run-workload/observability/events')) {
        return Promise.resolve({ ok: true, json: async () => ({ events: [], truncated: false }) } as Response);
      }
      if (url.includes('/agent-runs/agent-run-workload/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => 'workload stdout tail\n' } as unknown as Response);
      }
      if (url.includes('/agent-runs/agent-run-workload/logs/stdout')) {
        return Promise.resolve({ ok: true, text: async () => 'workload stdout\n' } as unknown as Response);
      }
      if (url.includes('/agent-runs/agent-run-workload/logs/stderr')) {
        return Promise.resolve({ ok: true, text: async () => 'workload stderr\n' } as unknown as Response);
      }
      if (url.includes('/agent-runs/agent-run-workload/diagnostics')) {
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

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

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
      workflowType: 'MoonMind.UserWorkflow',
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

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
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
      workflowType: 'MoonMind.UserWorkflow',
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
      if (url.includes('/agent-runs/agent-run-step-1/observability-summary')) {
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
      if (url.includes('/agent-runs/agent-run-step-1/observability/events')) {
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
      if (url.includes('/agent-runs/agent-run-step-1/logs/merged')) {
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

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/agent-runs/agent-run-step-1/observability-summary')),
    ).toBe(false);

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: 'Summary' }).length).toBeGreaterThan(0);
      expect(screen.getByRole('heading', { name: 'Checks' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Logs & Diagnostics' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Artifacts' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Metadata' })).toBeTruthy();
      expect(screen.getByText('Auto-approved')).toBeTruthy();
      expect(screen.getByText('art-step-summary')).toBeTruthy();
      expect(screen.getByText('child-wf-1')).toBeTruthy();
      expect(screen.getByText('step scoped log line')).toBeTruthy();
      expect(screen.getByRole('button', { name: 'Hide details for Apply patch' })).toBeTruthy();
    });
    expect(screen.getAllByText('approval policy: passed')[0]?.className).toContain('check-passed');

    // MM-920: the embedded step logs drop the redundant left rail and the second
    // "Live Logs" disclosure, so the log content sits directly under the
    // "Logs & Diagnostics" heading.
    const logsHeading = screen.getByRole('heading', { name: 'Logs & Diagnostics' });
    expect(logsHeading.closest('section')?.className).toContain('step-tl-detail-section--logs');
    expect(screen.queryByText('Live Logs')).toBeNull();

    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/agent-runs/agent-run-step-1/observability-summary')),
    ).toBe(true);
  });

  it('keeps unbound rows free of agent-run requests and upgrades expanded rows when agentRunId arrives later', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
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
                            agentRunId: null,
                          },
                        }
                      : step,
                  ),
                }
              : latestStepsSnapshot,
        } as Response);
      }
      if (url.includes('/agent-runs/agent-run-step-1/observability-summary')) {
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
      if (url.includes('/agent-runs/agent-run-step-1/observability/events')) {
        return Promise.resolve({ ok: false, status: 404 } as Response);
      }
      if (url.includes('/agent-runs/agent-run-step-1/logs/merged')) {
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

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/agent-runs/agent-run-step-1/observability-summary')),
    ).toBe(false);

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    await waitFor(() => {
      expect(screen.getByText('attached after refresh')).toBeTruthy();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/agent-runs/agent-run-step-1/observability-summary')),
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
      workflowType: 'MoonMind.UserWorkflow',
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

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    await waitFor(() => {
      expect(screen.getByText('Retry count: 2')).toBeTruthy();
      expect(screen.getByText('art-review-2')).toBeTruthy();
      expect(screen.getAllByText('approval policy: failed')[0]).toBeTruthy();
    });
  });

  it('resolves step-level agent-run routes against apiBase', async () => {
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
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Workflow behind apiBase',
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
      if (url.includes('/tenant/api/agent-runs/agent-run-step-1/observability-summary')) {
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
      if (url.includes('/tenant/api/agent-runs/agent-run-step-1/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ events: [], truncated: false }),
        } as Response);
      }
      if (url.includes('/tenant/api/agent-runs/agent-run-step-1/logs/merged')) {
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

    renderWithClient(<WorkflowDetailPage payload={apiBasePayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([url]) =>
          String(url).includes('/tenant/api/agent-runs/agent-run-step-1/observability-summary'),
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
      agentRunId: 'agent-run-root',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
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
      if (url.includes('/agent-runs/agent-run-root/observability-summary')) {
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
      if (url.includes('/agent-runs/agent-run-root/observability/events')) {
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
      if (url.includes('/agent-runs/agent-run-root/logs/merged')) {
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

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Steps: 403 (/api/executions/test-123/steps)')).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Observation' })).toBeTruthy();
    });

    fireEvent.click(screen.getByText('Live Logs'));

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([url]) => String(url).includes('/agent-runs/agent-run-root/observability-summary')),
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
      workflowType: 'MoonMind.UserWorkflow',
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

    renderWithClient(<WorkflowDetailPage payload={logStreamingDisabledPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Show details for Apply patch' }));

    await waitFor(() => {
      expect(screen.getByText(/live log streaming is disabled in the server dashboard config/i)).toBeTruthy();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/agent-runs/agent-run-step-1/observability-summary')),
    ).toBe(false);
  });

  it('renders loading state initially', () => {
    fetchSpy.mockImplementation(() => new Promise(() => {}));
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);
    expect(screen.getByText(/Loading workflow/i)).toBeTruthy();
  });

  it('builds canonical Temporal task editing routes', () => {
    expect(taskEditHref('mm:wf 1')).toBe('/workflows/new?editExecutionId=mm%3Awf%201');
    expect(taskEditForRerunHref('mm:wf 1')).toBe(
      '/workflows/new?rerunExecutionId=mm%3Awf%201&mode=edit',
    );
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
      workflowType: 'MoonMind.UserWorkflow',
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

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    const menu = await openWorkflowActionsMenu();
    expect(within(menu).getByRole('menuitem', { name: 'Edit' }).getAttribute('href')).toBe(
      '/workflows/new?editExecutionId=test-123',
    );
    const editLink = within(menu).getByRole('menuitem', { name: 'Edit' });
    editLink.addEventListener('click', (event) => event.preventDefault());
    fireEvent.focus(editLink);
    fireEvent.keyDown(editLink, { key: 'Enter' });
    fireEvent.click(await screen.findByRole('button', { name: 'Workflow actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Rerun' }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/update',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ updateName: 'RequestRerun' }),
        }),
      );
    });
    expect(navigateTo).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe('/workflows/test-123/steps');
    expect(window.location.search).toBe('?source=temporal');
    expect(
      window.sessionStorage.getItem('moonmind.temporalTaskEditing.notice'),
    ).toBeNull();
    expect(
      await screen.findByText('Rerun was requested and the latest execution view is ready.'),
    ).toBeTruthy();
    expect(screen.getByRole('status')).toBeTruthy();
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

  it('opens a Workflow actions menu with labels for every currently available workflow operation', async () => {
    // Remediate is only available on the Artifacts surface where its create-preview
    // controls render, so exercise the full menu from the Artifacts subroute.
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
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
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Action menu task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      attentionRequired: true,
      blockedOnDependencies: true,
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canSetTitle: true,
        canUpdateInputs: true,
        canEditForRerun: true,
        canRerun: true,
        canResumeFromFailedStep: true,
        canPause: true,
        canResume: true,
        canApprove: true,
        canReject: true,
        canCancel: true,
        canSendMessage: true,
        canBypassDependencies: true,
      },
      resume: {
        available: true,
        checkpointRef: 'artifact://checkpoint/source',
        failedStepId: 'implement',
        sourceRunId: '01-run',
      },
      stepsHref: '/api/executions/test-123/steps',
    };
    const stepLedger = {
      workflowId: 'test-123',
      runId: '01-run',
      runScope: 'latest',
      steps: [
        {
          logicalStepId: 'plan',
          order: 1,
          title: 'Plan',
          status: 'succeeded',
          executionOrdinal: 1,
          updatedAt: '2026-03-28T00:00:01Z',
          refs: {},
          artifacts: { outputSummary: 'artifact://summary/plan' },
          recoveryPreservation: {
            eligible: true,
            reason: 'complete',
            message: 'Step has recoverable output refs and state checkpoint evidence.',
          },
        },
      ],
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => stepLedger } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/remediations?direction=')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    const trigger = await screen.findByRole('button', { name: 'Workflow actions' });
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(trigger);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    const menu = screen.getByRole('menu', { name: 'Workflow actions' });
    for (const label of [
      'Rename',
      'Edit',
      'Compare run',
      'Rerun',
      'Resume from failed step',
      'Recover from selected step',
      'Pause',
      'Resume',
      'Approve',
      'Reject',
      'Cancel',
      'Send Message',
      'Bypass Dependencies',
      'Remediate',
    ]) {
      expect(within(menu).getByRole('menuitem', { name: label })).toBeTruthy();
    }
  });

  it('hides the Remediate action off the Artifacts surface where its controls are unavailable', async () => {
    // The remediation mode/authority/action-policy controls only render on the Artifacts
    // tab; surfacing Remediate elsewhere would submit default settings without operator choice.
    window.history.pushState({}, 'Steps Test', '/workflows/test-123/steps?source=temporal');
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
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Action menu task',
      summary: 'Execution summary',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canSetTitle: true,
        canCancel: true,
      },
      stepsHref: '/api/executions/test-123/steps',
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => ({ steps: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/remediations?direction=')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    const trigger = await screen.findByRole('button', { name: 'Workflow actions' });
    fireEvent.click(trigger);
    const menu = screen.getByRole('menu', { name: 'Workflow actions' });
    expect(within(menu).getByRole('menuitem', { name: 'Cancel' })).toBeTruthy();
    expect(within(menu).queryByRole('menuitem', { name: 'Remediate' })).toBeNull();
  });

  it('routes menu selections through existing handlers and preserves destructive confirmations', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Action route task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canSetTitle: true,
        canPause: true,
        canCancel: true,
      },
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (init?.method === 'POST') {
        return Promise.resolve({ ok: true, json: async () => ({ accepted: true }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Workflow actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }));
    fireEvent.change(screen.getByLabelText('Workflow title'), {
      target: { value: 'Updated title' },
    });
    confirmWorkflowDialog('Rename workflow');
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/update',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ updateName: 'SetTitle', title: 'Updated title' }),
        }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: 'Workflow actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Pause' }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/signal',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ signalName: 'Pause', payload: {} }),
        }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: 'Workflow actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(
      fetchSpy.mock.calls.some(([url, init]) => String(url).includes('/cancel') && init?.method === 'POST'),
    ).toBe(false);
  });

  it('dismisses the Workflow actions menu without side effects and supports keyboard selection', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Keyboard task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canPause: true,
        canResume: true,
      },
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (init?.method === 'POST') {
        return Promise.resolve({ ok: true, json: async () => ({ accepted: true }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    const trigger = await screen.findByRole('button', { name: 'Workflow actions' });
    fireEvent.keyDown(trigger, { key: 'Enter' });
    expect(screen.getByRole('menu', { name: 'Workflow actions' })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('menu', { name: 'Workflow actions' }), { key: 'Escape' });
    expect(screen.queryByRole('menu', { name: 'Workflow actions' })).toBeNull();
    expect(fetchSpy.mock.calls.some(([url, init]) => String(url).includes('/signal') && init?.method === 'POST')).toBe(false);
    expect(document.activeElement).toBe(trigger);

    fireEvent.click(trigger);
    expect(screen.getByRole('menu', { name: 'Workflow actions' })).toBeTruthy();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('menu', { name: 'Workflow actions' })).toBeNull();
    expect(fetchSpy.mock.calls.some(([url, init]) => String(url).includes('/signal') && init?.method === 'POST')).toBe(false);

    fireEvent.click(trigger);
    const focusMenu = screen.getByRole('menu', { name: 'Workflow actions' });
    fireEvent.blur(focusMenu, { relatedTarget: document.body });
    expect(screen.queryByRole('menu', { name: 'Workflow actions' })).toBeNull();
    expect(fetchSpy.mock.calls.some(([url, init]) => String(url).includes('/signal') && init?.method === 'POST')).toBe(false);

    fireEvent.keyDown(trigger, { key: ' ' });
    const menu = screen.getByRole('menu', { name: 'Workflow actions' });
    fireEvent.keyDown(menu, { key: 'ArrowDown' });
    fireEvent.keyDown(menu, { key: 'Enter' });
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/signal',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ signalName: 'Resume', payload: {} }),
        }),
      );
    });
  });

  it('recalculates menu entries from refreshed actions and blocks unavailable actions', async () => {
    const executions = [
      {
        taskId: 'test-123',
        workflowId: 'test-123',
        namespace: 'default',
        temporalRunId: '01-run',
        runId: '01-run',
        source: 'temporal',
        workflowType: 'MoonMind.UserWorkflow',
        title: 'Refresh task',
        summary: 'Execution summary',
        status: 'running',
        state: 'executing',
        rawState: 'executing',
        temporalStatus: 'running',
        createdAt: '2026-03-28T00:00:00Z',
        updatedAt: '2026-03-28T00:00:02Z',
        actions: { canPause: true },
      },
      {
        taskId: 'test-123',
        workflowId: 'test-123',
        namespace: 'default',
        temporalRunId: '01-run',
        runId: '01-run',
        source: 'temporal',
        workflowType: 'MoonMind.UserWorkflow',
        title: 'Refresh task',
        summary: 'Execution summary',
        status: 'running',
        state: 'executing',
        rawState: 'executing',
        temporalStatus: 'running',
        createdAt: '2026-03-28T00:00:00Z',
        updatedAt: '2026-03-28T00:00:03Z',
        actions: {
          canPause: false,
          disabledReasons: { canPause: 'state_not_eligible' },
        },
      },
    ];
    let detailRequests = 0;
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      const payload = executions[Math.min(detailRequests, executions.length - 1)];
      detailRequests += 1;
      return Promise.resolve({ ok: true, json: async () => payload } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={{
      ...actionsPayload,
      initialData: {
        dashboardConfig: {
          pollIntervalsMs: { detail: 1 },
          features: {
            temporalDashboard: {
              actionsEnabled: true,
            },
          },
        },
      },
    }} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Workflow actions' }));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
    await waitFor(() => {
      const pauseItem = screen.getByRole('menuitem', { name: /Pause/ });
      expect(within(pauseItem).getByText('state not eligible')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('menuitem', { name: /Pause/ }));
    expect(fetchSpy.mock.calls.some(([url, init]) => String(url).includes('/signal') && init?.method === 'POST')).toBe(false);
  });

  it('shows an empty Workflow actions menu state when no workflow actions are available', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'No action task',
      summary: 'Execution summary',
      status: 'completed',
      state: 'completed',
      rawState: 'completed',
      temporalStatus: 'completed',
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

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Workflow actions' }));
    expect(screen.getByText('No workflow actions are currently available.')).toBeTruthy();
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
      workflowType: 'MoonMind.UserWorkflow',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
      workflowType: 'MoonMind.UserWorkflow',
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
    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    let menu = await openWorkflowActionsMenu();
    expect(within(menu).getByRole('menuitem', { name: 'Rerun' })).toBeTruthy();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Rename' }));
    fireEvent.change(screen.getByLabelText('Workflow title'), {
      target: { value: 'Renamed task' },
    });
    confirmWorkflowDialog('Rename workflow');

    await waitFor(() => {
      expect(fetchSpy.mock.calls.some(([input]) => String(input).includes('/update'))).toBe(true);
    });

    menu = await openWorkflowActionsMenu();
    const rerunItem = within(menu).getByRole('menuitem', { name: /Rerun/ });
    expect(rerunItem.getAttribute('aria-disabled')).toBe('true');
    expect(within(rerunItem).getByText('action pending')).toBeTruthy();
    fireEvent.click(rerunItem);
    const updateCalls = fetchSpy.mock.calls.filter(([input]) => String(input).includes('/update'));
    expect(updateCalls).toHaveLength(1);
  });

  it('shows failed workflow Edit task and Rerun entry points when capabilities allow them', async () => {
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
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Failed workflow',
      summary: 'Execution summary',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canEditForRerun: true,
        canRerun: true,
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    const menu = await openWorkflowActionsMenu();
    expect(within(menu).getByRole('menuitem', { name: 'Edit' }).getAttribute('href')).toBe(
      '/workflows/new?rerunExecutionId=test-123&mode=edit',
    );
    expect(within(menu).getByRole('menuitem', { name: 'Rerun' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Rerun' })).toBeNull();
  });

  it('does not show edit unavailable text while another edit path is enabled', async () => {
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
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Running task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canUpdateInputs: true,
        canEditForRerun: false,
        disabledReasons: {
          canEditForRerun: 'state_not_eligible',
        },
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    const menu = await openWorkflowActionsMenu();
    const editItem = within(menu).getByRole('menuitem', { name: 'Edit' });
    expect(editItem.getAttribute('href')).toBe('/workflows/new?editExecutionId=test-123');
    expect(within(editItem).queryByText('state not eligible')).toBeNull();
  });

  it('shows failed workflow edit-for-rerun disabled reasons without inferring from status', async () => {
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
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Failed workflow without snapshot',
      summary: 'Execution summary',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canEditForRerun: false,
        canRerun: false,
        disabledReasons: {
          canEditForRerun: 'original_task_input_snapshot_missing',
          canRerun: 'original_task_input_snapshot_missing',
        },
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    const menu = await openWorkflowActionsMenu();
    expect(screen.queryByRole('link', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Rerun' })).toBeNull();
    const disabledEditItem = within(menu).getByRole('menuitem', { name: 'Edit' });
    expect(
      within(disabledEditItem).getByText('original task input snapshot missing'),
    ).toBeTruthy();
    const disabledRerunItem = within(menu).getByRole('menuitem', { name: 'Rerun' });
    expect(
      within(disabledRerunItem).getByText('original task input snapshot missing'),
    ).toBeTruthy();
  });

  it('renders failed-step Resume separately from lifecycle Resume and submits the resume command', async () => {
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
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Failed workflow',
      summary: 'Execution summary',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canResume: false,
        canResumeFromFailedStep: true,
        canRerun: true,
      },
      resume: {
        available: true,
        checkpointRef: 'artifact://checkpoint/source',
        failedStepId: 'implement',
        sourceRunId: '01-run',
      },
      relatedRuns: [
        {
          workflowId: 'mm:source',
          runId: 'run-source',
          relationship: 'Resumed from failed step',
          status: 'failed',
          href: '/workflows/mm:source',
        },
      ],
    };
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(init === undefined ? { url } : { url, init });
      if (url.includes('/recover-from-failed-step')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ accepted: true }),
        } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    const menu = await openWorkflowActionsMenu();
    const resumeButton = within(menu).getByRole('menuitem', { name: 'Resume from failed step' });
    expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull();
    fireEvent.click(resumeButton);
    confirmWorkflowDialog('Resume workflow');

    await waitFor(() => {
      expect(calls.some((call) => call.url.includes('/recover-from-failed-step'))).toBe(true);
    });
    const resumeCall = calls.find((call) => call.url.includes('/recover-from-failed-step'));
    expect(JSON.parse(String(resumeCall?.init?.body || '{}')).recoveryCheckpointRef).toBe(
      'artifact://checkpoint/source',
    );
  });

  it('submits selected-step recovery with pinned source identity', async () => {
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
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Failed workflow',
      summary: 'Execution summary',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      stepsHref: '/api/executions/test-123/steps',
      actions: {
        canResume: false,
        canResumeFromFailedStep: true,
      },
      resume: {
        available: true,
        checkpointRef: 'artifact://checkpoint/source',
        failedStepId: 'implement',
        sourceRunId: '01-run',
      },
      relatedRuns: [],
    };
    const stepLedger = {
      workflowId: 'test-123',
      runId: '01-run',
      runScope: 'latest',
      steps: [
        {
          logicalStepId: 'plan',
          order: 1,
          title: 'Plan',
          status: 'succeeded',
          executionOrdinal: 1,
          updatedAt: '2026-03-28T00:00:01Z',
          refs: {},
          artifacts: { outputSummary: 'artifact://summary/plan' },
          recoveryPreservation: {
            eligible: true,
            reason: 'complete',
            message: 'Step has recoverable output refs and state checkpoint evidence.',
          },
        },
        {
          logicalStepId: 'implement',
          order: 2,
          title: 'Implement',
          status: 'failed',
          executionOrdinal: 1,
          updatedAt: '2026-03-28T00:00:02Z',
          refs: {},
          artifacts: {},
          recoveryPreservation: {
            eligible: false,
            reason: 'not_completed',
            message: 'Step is not completed and cannot be preserved for Resume.',
          },
        },
        {
          logicalStepId: 'publish',
          order: 3,
          title: 'Publish',
          status: 'pending',
          executionOrdinal: 0,
          updatedAt: '2026-03-28T00:00:03Z',
          refs: {},
          artifacts: {},
          recoveryPreservation: {
            eligible: false,
            reason: 'not_completed',
            message: 'Step is not completed and cannot be preserved for Resume.',
          },
        },
      ],
    };
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(init === undefined ? { url } : { url, init });
      if (url.includes('/recover-from-selected-step')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ accepted: true }),
        } as Response);
      }
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => stepLedger } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    const select = await screen.findByLabelText('Recovery start step');
    fireEvent.change(select, { target: { value: 'plan' } });
    await waitFor(() => {
      expect((select as HTMLSelectElement).value).toBe('plan');
    });
    expect(
      (screen.getByRole('option', { name: /Publish - after failed step/ }) as HTMLOptionElement).disabled,
    ).toBe(true);
    const menu = await openWorkflowActionsMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Recover from selected step' }));
    confirmWorkflowDialog('Recover workflow');

    await waitFor(() => {
      expect(calls.some((call) => call.url.includes('/recover-from-selected-step'))).toBe(true);
    });
    const selectedCall = calls.find((call) => call.url.includes('/recover-from-selected-step'));
    const body = JSON.parse(String(selectedCall?.init?.body || '{}'));
    expect(body).toMatchObject({
      sourceWorkflowId: 'test-123',
      sourceRunId: '01-run',
      selectedStartStepId: 'plan',
      recoveryCheckpointRef: 'artifact://checkpoint/source',
    });
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
      workflowType: 'MoonMind.UserWorkflow',
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

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Flagged off task')).toBeTruthy();
    });
    expect(screen.queryByRole('link', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Rerun' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Workflow Actions' })).toBeNull();
  });

  it('renders workflow details on successful fetch', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      targetRuntime: 'gemini_cli',
      targetSkill: 'jira-pr-verify',
      taskSkills: ['jira-pr-verify', 'fix-comments'],
      skillRuntime: {
        resolvedSkillsetRef: 'artifact:resolved-skills-1',
        selectedSkills: ['jira-pr-verify'],
        selectedEvidence: [
          {
            name: 'jira-pr-verify',
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
      priority: 4,
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
      recurrence: {
        definitionId: 'schedule-alpha',
        href: '/schedules/schedule-alpha',
      },
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Example task')).toBeTruthy();
      expect(screen.getByText('Did work')).toBeTruthy();
      expect(screen.getByText('Gemini CLI')).toBeTruthy();
      expect(screen.getByText('Explicit Selection').closest('div')?.textContent).toContain(
        'jira-pr-verify, fix-comments',
      );
      expect(screen.getByText('Delegated Skill').closest('div')?.textContent).toContain('jira-pr-verify');
      expect(screen.getByText('Selected Skill Evidence').closest('div')?.textContent).toContain(
        'jira-pr-verify',
      );
      expect(screen.getByText('Selected Skill Evidence').closest('div')?.textContent).toContain(
        'artifact:skill-body-1',
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
      expect(screen.getByText('Priority').closest('div')?.textContent).toContain('4');
      expect(screen.getByRole('link', { name: 'https://github.com/MoonLadderStudios/MoonMind/pull/123' })).toBeTruthy();
      expect(screen.getByText(/Created by schedule/)).toBeTruthy();
      expect(screen.getByRole('link', { name: 'schedule-alpha' }).getAttribute('href')).toBe('/schedules/schedule-alpha');
    });

    expect(screen.queryByText('Inspect the repository.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Show Workflow Inputs/ }));
    expect(screen.getByRole('button', { name: /Hide Workflow Inputs/ }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(/Inspect the repository\./)).toBeTruthy();
    expect(screen.getByText(/Then run the focused UI tests\./)).toBeTruthy();

    expect(fetchSpy).toHaveBeenCalledWith('/api/executions/test-123?source=temporal');
  });

  it.each([
    {
      label: 'model is the canonical resolved value',
      payload: { model: 'gpt-5-codex' },
    },
    {
      label: 'backend-normalized model is shown when resolvedModel is also set',
      payload: { model: 'gpt-5-codex', resolvedModel: 'gpt-5-codex' },
    },
    {
      label: 'backend-normalized model is shown when only requestedModel was originally set',
      payload: { model: 'gpt-5-codex', requestedModel: 'gpt-5-codex' },
    },
  ])('displays the Model fact consistently — $label', async ({ payload }) => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      targetRuntime: 'codex_cli',
      title: 'Model display task',
      summary: 'Verifies model is shown',
      status: 'completed',
      state: 'succeeded',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: { canSetTitle: false, canCancel: false, canRerun: false },
      ...payload,
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/remediations?direction=')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ direction: 'inbound', items: [] }),
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      const modelFact = screen.getByText('Model').closest('div');
      expect(modelFact?.textContent).toContain('gpt-5-codex');
    });
  });

  it('hides the Model fact when no model field is populated', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      targetRuntime: 'codex_cli',
      title: 'No model task',
      summary: 'No model resolved',
      status: 'completed',
      state: 'succeeded',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: { canSetTitle: false, canCancel: false, canRerun: false },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/remediations?direction=')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ direction: 'inbound', items: [] }),
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('No model task')).toBeTruthy();
    });
    expect(screen.queryByText('Model')).toBeNull();
  });

  it('renders target attachment diagnostics without replacing raw diagnostics', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      agentRunId: '123e4567-e89b-12d3-a456-426614174000',
      title: 'Target diagnostic task',
      summary: 'Attachment preparation degraded.',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      targetDiagnostics: {
        targets: [
          {
            targetKind: 'objective',
            label: 'Workflow objective',
            attachments: [
              {
                artifactRef: 'artifact://input/objective',
                filename: 'objective.png',
                contentType: 'image/png',
                previewAvailable: true,
              },
            ],
            refs: [{ refKind: 'attachment_manifest', artifactRef: 'artifact://diagnostics/input-manifest' }],
            failures: [],
          },
          {
            targetKind: 'step',
            stepId: 'inspect',
            label: 'Inspect screenshot',
            attachments: [],
            refs: [],
            failures: [
              {
                phase: 'materialization',
                message: 'Attachment download failed before step execution.',
                evidenceRef: 'artifact://diagnostics/prepare',
              },
            ],
          },
        ],
        recovery: {
          resumed: true,
          sourceWorkflowId: 'mm:source',
          sourceRunId: 'run-source',
          checkpointRef: 'artifact://resume/checkpoint',
          preservedSteps: [
            {
              logicalStepId: 'prepare',
              title: 'Prepare context',
              sourceExecutionOrdinal: 1,
              sourceWorkflowId: 'mm:source',
              sourceRunId: 'run-source',
            },
          ],
          failedRecoveryPhase: null,
        },
        degradedReason: 'step_attachment_missing',
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/diagnostics')) {
        return Promise.resolve({ ok: true, text: async () => '{"raw":"diagnostics"}' } as Response);
      }
      if (url.includes('/logs/') || url.includes('/observability')) {
        return Promise.resolve({ ok: true, text: async () => '' } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Target diagnostic task')).toBeTruthy();
    });
    expect(screen.getByRole('heading', { name: 'Target Diagnostics' })).toBeTruthy();
    expect(screen.getAllByText('Workflow objective').length).toBeGreaterThan(0);
    expect(screen.getAllByText('objective.png').length).toBeGreaterThan(0);
    expect(screen.getByText('Inspect screenshot')).toBeTruthy();
    expect(screen.getByText('Attachment download failed before step execution.')).toBeTruthy();
    expect(screen.getByText('artifact://diagnostics/input-manifest')).toBeTruthy();
    expect(screen.getByText('Resumed from mm:source')).toBeTruthy();
    expect(screen.getByText('Prepare context')).toBeTruthy();
    expect(screen.getAllByText('artifact://resume/checkpoint').length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: 'Recovery evidence' })).toBeTruthy();
    expect(screen.getByText('Preserved provenance')).toBeTruthy();

    fireEvent.click(screen.getByText('Diagnostics'));
    await waitFor(() => {
      expect(screen.getByText(/\{"raw":"diagnostics"\}/)).toBeTruthy();
    });
  });

  it('renders empty target and generated context diagnostics', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      agentRunId: '123e4567-e89b-12d3-a456-426614174000',
      title: 'Generated context diagnostic task',
      summary: 'Context preparation completed.',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      targetDiagnostics: {
        targets: [
          {
            targetKind: 'objective',
            label: 'Workflow objective',
            attachments: [
              {
                artifactRef: 'artifact://input/objective',
                filename: 'objective.png',
                contentType: 'image/png',
                previewAvailable: true,
              },
            ],
            refs: [{ refKind: 'generated_context', artifactRef: 'artifact://context/objective' }],
            failures: [],
          },
          {
            targetKind: 'step',
            stepId: 'inspect',
            label: 'Inspect screenshot',
            attachments: [],
            refs: [{ refKind: 'generated_context', artifactRef: 'artifact://context/inspect' }],
            failures: [],
          },
        ],
        recovery: null,
        degradedReason: null,
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/logs/') || url.includes('/observability') || url.includes('/diagnostics')) {
        return Promise.resolve({ ok: true, text: async () => '' } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Generated context diagnostic task')).toBeTruthy();
    });
    expect(screen.getByText('artifact://context/objective')).toBeTruthy();
    expect(screen.getByText('artifact://context/inspect')).toBeTruthy();
    expect(screen.getByText('No attachments recorded for this target.')).toBeTruthy();
  });

  it('renders failed-step execution Resume phase with preserved provenance', async () => {
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      agentRunId: '123e4567-e89b-12d3-a456-426614174000',
      title: 'Failed Resume diagnostic task',
      summary: 'Resume failed during step execution.',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      targetDiagnostics: {
        targets: [],
        recovery: {
          resumed: true,
          sourceWorkflowId: 'mm:source',
          sourceRunId: 'run-source',
          checkpointRef: 'artifact://resume/checkpoint',
          preservedSteps: [
            {
              logicalStepId: 'prepare',
              title: 'Prepare context',
              sourceExecutionOrdinal: 1,
              sourceWorkflowId: 'mm:source',
              sourceRunId: 'run-source',
            },
          ],
          failedRecoveryPhase: 'failed_step_execution',
        },
        degradedReason: null,
      },
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/logs/') || url.includes('/observability') || url.includes('/diagnostics')) {
        return Promise.resolve({ ok: true, text: async () => '' } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Failed Resume diagnostic task')).toBeTruthy();
    });
    expect(screen.getByText('Resumed from mm:source')).toBeTruthy();
    expect(screen.getByText('Prepare context')).toBeTruthy();
    expect(screen.getByText('Failed phase:')).toBeTruthy();
    expect(screen.getByText('Failed step execution')).toBeTruthy();
  });

  it('renders remediation create action, relationships, evidence, and degraded states', async () => {
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      title: 'Failed target task',
      summary: 'Needs remediation.',
      status: 'failed',
      state: 'failed',
      rawState: 'failed',
      temporalStatus: 'failed',
      repository: 'MoonLadderStudios/MoonMind',
      targetRuntime: 'claude_code',
      model: 'claude-opus-4-1-20250805',
      effort: 'high',
      profileId: 'claude_anthropic',
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

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    const menu = await openWorkflowActionsMenu();
    expect(within(menu).getByRole('menuitem', { name: 'Remediate' })).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Remediation' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Remediation Workflows' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Remediation Target' })).toBeTruthy();
    expect(screen.getByText('mm:remediation-1')).toBeTruthy();
    expect(screen.getByText('mm:target-1')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Remediation Evidence' })).toBeTruthy();
    expect(screen.getByText('Context')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Evidence' }).getAttribute('href')).toBe(
      '/api/artifacts/art_context/download',
    );

    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Remediate' }));

    await waitFor(() => {
      const remediationCreateCall = fetchSpy.mock.calls.find(
        ([url, init]) => String(url) === '/api/executions/test-123/remediation' && init?.method === 'POST',
      );
      expect(remediationCreateCall).toBeTruthy();
      const remediationBody = JSON.parse(String(remediationCreateCall?.[1]?.body));
      expect(remediationBody).not.toHaveProperty('targetRuntime');
      expect(remediationBody).not.toHaveProperty('profileId');
      expect(remediationBody).toMatchObject({
        repository: 'MoonLadderStudios/MoonMind',
        runtime: {
          mode: 'claude_code',
          model: 'claude-opus-4-1-20250805',
          effort: 'high',
          profileId: 'claude_anthropic',
        },
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
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-remediation-create-choices/artifacts?source=temporal');
    const mockExecution = {
      taskId: 'test-remediation-create-choices',
      workflowId: 'test-remediation-create-choices',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
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

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

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
    const menu = await openWorkflowActionsMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Remediate' }));

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
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-complete/artifacts?source=temporal');
    const mockExecution = {
      taskId: 'test-complete',
      workflowId: 'test-complete',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
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

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Completed target task')).toBeTruthy();
    });

    expect(screen.queryByRole('button', { name: 'Remediate' })).toBeNull();
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/remediations?direction=inbound'))).toBe(true);
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/remediations?direction=outbound'))).toBe(true);
  });

  it('renders approval-gated remediation as read-only when the operator cannot decide', async () => {
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-readonly-approval/artifacts?source=temporal');
    const mockExecution = {
      taskId: 'test-readonly-approval',
      workflowId: 'test-readonly-approval',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
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

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

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
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-remediation-degraded/artifacts?source=temporal');
    const mockExecution = {
      taskId: 'test-remediation-degraded',
      workflowId: 'test-remediation-degraded',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
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

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Remediation' })).toBeTruthy();
    expect(screen.getByText('No inbound remediation workflows linked yet.')).toBeTruthy();
    expect(screen.getByText('Evidence bundle is missing.')).toBeTruthy();
    expect(screen.getByText('Live follow is unavailable; durable remediation artifacts remain authoritative.')).toBeTruthy();
    expect(screen.getByText('No remediation evidence artifacts linked yet.')).toBeTruthy();

    const longTarget = screen.getByText('mm:target-long-workflow-id-with-many-segments-for-mobile-containment');
    expect(longTarget.closest('code')?.className).toContain('break-all');
  });

  it('renders rich remediation target metadata for selected steps, live observation, evidence degradation, and locks', async () => {
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-remediation-rich/artifacts?source=temporal');
    const mockExecution = {
      taskId: 'test-remediation-rich',
      workflowId: 'test-remediation-rich',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      title: 'Rich remediation task',
      summary: 'Remediation work with live observation.',
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
      if (url.includes('/executions/test-remediation-rich/remediations?direction=inbound')) {
        return Promise.resolve({ ok: true, json: async () => ({ direction: 'inbound', items: [] }) } as Response);
      }
      if (url.includes('/executions/test-remediation-rich/remediations?direction=outbound')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            direction: 'outbound',
            items: [richOutboundRemediationLink()],
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

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Remediation Target' })).toBeTruthy();
    expect(screen.getByText('mm:target-rich')).toBeTruthy();
    expect(screen.getByText('collect-context, repair-runtime')).toBeTruthy();
    expect(screen.getByText('awaiting_external')).toBeTruthy();
    expect(screen.getByText('inspect_context, request_approval, terminate_session')).toBeTruthy();
    expect(screen.getByText(/Unavailable: runtime_stderr, provider_snapshot/)).toBeTruthy();
    expect(screen.getByText('Live observation active')).toBeTruthy();
    expect(screen.getByText('stdout:42')).toBeTruthy();
    expect(screen.getByText('reconnected')).toBeTruthy();
    expect(screen.getByText('run-target-rich:2')).toBeTruthy();
    expect(screen.getByText('conflict')).toBeTruthy();
    expect(screen.getAllByText('test-remediation-rich').length).toBeGreaterThan(0);
    expect(screen.queryByText('/var/lib/moonmind/raw-context.json')).toBeNull();
  });

  it('keeps remediation panels accessible and contained in dashboard CSS', async () => {
    const { readFileSync } = await import('node:fs');
    const dashboardCss = readFileSync(
      `${process.cwd()}/frontend/src/styles/dashboard.css`,
      'utf8',
    );

    expect(dashboardCss).toMatch(/\.td-remediation-region:focus-within\s*\{[^}]*outline:\s*2px solid/s);
    expect(dashboardCss).toMatch(/\.td-remediation-list\s+\.card\s*\{[^}]*min-width:\s*0;[^}]*max-width:\s*100%;/s);
    expect(dashboardCss).toMatch(/@media\s*\(max-width:\s*720px\)\s*\{[^}]*\.td-remediation-region/s);
    expect(dashboardCss).toMatch(/\.td-remediation-list\s+code\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
  });

  it('renders workflow detail as separated matte evidence and action regions', async () => {
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      title: 'Example task',
      summary: 'Did work',
      taskInstructions: 'Inspect the repository.',
      status: 'completed',
      state: 'succeeded',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      closeStatus: 'COMPLETED',
      stepsHref: '/api/executions/test-123/steps',
      agentRunId: 'agent-run-1',
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

    renderWithClient(<WorkflowDetailPage payload={mm428CompositionPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Example task')).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Workflow Artifacts' })).toBeTruthy();
      expect(screen.getByText('artifact-output')).toBeTruthy();
    });

    const root = document.querySelector<HTMLElement>('.workflow-detail-page');
    const artifacts = document.querySelector<HTMLElement>('.td-artifacts-region.td-evidence-region');

    expect(root).not.toBeNull();
    expect(artifacts).not.toBeNull();
    expect(artifacts?.querySelector('.td-evidence-slab.queue-table-wrapper')).not.toBeNull();
    expect(root?.querySelector('.td-evidence-region .panel--floating')).toBeNull();
    expect(root?.querySelector('.td-evidence-region .queue-floating-bar')).toBeNull();
  });

  it('renders empty skill provenance when task skill metadata is missing', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Example task')).toBeTruthy();
    });

    expect(screen.getByText('Explicit Selection').closest('div')?.textContent).toContain('None');
    expect(screen.getByText('Delegated Skill').closest('div')?.textContent).toContain('—');
  });

  it('renders structured run summary details from the summary artifact', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
    window.history.pushState({}, 'Overview Test', '/workflows/test-123?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Unsafe task')).toBeTruthy();
    });

    expect(screen.queryByText('PR Link')).toBeNull();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
  });

  it('renders merge automation visibility from the run summary', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-merge-visibility?source=temporal');
    const mockExecution = {
      taskId: 'test-merge-visibility',
      workflowId: 'test-merge-visibility',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      title: 'Merge visibility task',
      summary: 'Waiting on merge automation',
      status: 'running',
      state: 'awaiting_external',
      rawState: 'awaiting_external',
      temporalStatus: 'running',
      closeStatus: null,
      summaryArtifactRef: 'art-summary-merge',
      publishMode: 'pr_with_merge_automation',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getAllByText('Merge Automation').length).toBeGreaterThan(0);
      expect(screen.getByText('PR with Merge Automation')).toBeTruthy();
      expect(screen.queryByText('Selected')).toBeNull();
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
    window.history.pushState({}, 'Overview Test', '/workflows/test-live-merge-visibility?source=temporal');
    const mockExecution = {
      taskId: 'test-live-merge-visibility',
      workflowId: 'test-live-merge-visibility',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      title: 'Live merge visibility task',
      summary: 'Waiting on merge automation',
      status: 'running',
      state: 'awaiting_external',
      rawState: 'awaiting_external',
      temporalStatus: 'running',
      closeStatus: null,
      publishMode: 'pr_with_merge_automation',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
    window.history.pushState({}, 'Overview Test', '/workflows/test-null-merge-artifact-refs?source=temporal');
    const mockExecution = {
      taskId: 'test-null-merge-artifact-refs',
      workflowId: 'test-null-merge-artifact-refs',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      title: 'Null merge artifact refs task',
      summary: 'Waiting on merge automation',
      status: 'running',
      state: 'awaiting_external',
      rawState: 'awaiting_external',
      temporalStatus: 'running',
      closeStatus: null,
      publishMode: 'pr_with_merge_automation',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Null merge artifact refs task')).toBeTruthy();
      expect(screen.getByText('waiting')).toBeTruthy();
      expect(screen.getByText('merge-automation:test-null-merge-artifact-refs')).toBeTruthy();
    });
  });

  it('renders prerequisite and dependent panels for dependency-aware runs', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/mm%3Adependent-1?source=temporal');
    const mockExecution = {
      taskId: 'mm:dependent-1',
      workflowId: 'mm:dependent-1',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
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
          workflowType: 'MoonMind.UserWorkflow',
        },
      ],
      dependents: [
        {
          workflowId: 'mm:child-1',
          title: 'Run UI smoke tests',
          summary: 'Blocked on this task',
          state: 'waiting_on_dependencies',
          closeStatus: null,
          workflowType: 'MoonMind.UserWorkflow',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Dependencies' })).toBeTruthy();
      expect(screen.getByText(/Blocked on prerequisites/i)).toBeTruthy();
      expect(screen.getByText('Build shared schema')).toBeTruthy();
      expect(screen.getByText('Run UI smoke tests')).toBeTruthy();
    });
  });

  it('signals a manual dependency wait bypass from the dependency panel', async () => {
    const signalBodies: unknown[] = [];
    const mockExecution = {
      taskId: 'mm:dependent-1',
      workflowId: 'mm:dependent-1',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
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
          workflowType: 'MoonMind.UserWorkflow',
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

    window.history.pushState({}, 'Test', '/workflows/mm%3Adependent-1?source=temporal');
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    const menu = await openWorkflowActionsMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Bypass Dependencies' }));

    await waitFor(() => {
      expect(signalBodies).toEqual([
        {
          signalName: 'BypassDependencies',
          payload: { reason: 'Dependency wait bypassed by operator from the dashboard.' },
        },
      ]);
    });
  });

  it('renders artifact rows from snake_case temporal artifact payloads', async () => {
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Report' })).toBeTruthy();
      expect(screen.getByText('Final implementation report')).toBeTruthy();
      expect(screen.getByText('Summary JSON')).toBeTruthy();
      expect(screen.getByText('Screenshot evidence')).toBeTruthy();
    });

    const reportHeading = screen.getByRole('heading', { name: 'Report' });
    const artifactsHeading = screen.getByRole('heading', { name: 'Workflow Artifacts' });
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
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Generic artifact task')).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Workflow Artifacts' })).toBeTruthy();
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch workflow: Not Found/i)).toBeTruthy();
    });
  });

  it('decodes encoded task ids from the route before fetching', async () => {
    window.history.pushState({}, 'Encoded Test', '/workflows/mm%3Atest-123?source=temporal');

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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Encoded task')).toBeTruthy();
      expect(screen.getByText('Workflow mm:test-123')).toBeTruthy();
    });

    expect(fetchSpy).toHaveBeenCalledWith('/api/executions/mm%3Atest-123?source=temporal');
  });

  it('shows launch-waiting message when no agentRunId is present yet', async () => {
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(
        screen.getByText(/Waiting for managed runtime launch to create live logs/i),
      ).toBeTruthy();
    });
  });

  it('renders artifact download link using explicit downloadUrl when present', async () => {
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Artifact task with download_url')).toBeTruthy();
      expect(screen.getByText('art-with-url')).toBeTruthy();
      expect(screen.getByRole('link', { name: /Download/i }).getAttribute('href')).toBe(
        'https://external-storage.com/art-with-url'
      );
    });
  });

  it('groups task image inputs by persisted target and preserves download when preview fails', async () => {
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
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
                  source: 'workflow-console-objective-attachment',
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
                  source: 'workflow-console-step-attachment',
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
                  source: 'workflow-console-step-attachment',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Input Images' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Objective' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Step 2' })).toBeTruthy();
      expect(screen.getAllByRole('heading', { name: 'Step 2' })).toHaveLength(1);
      expect(screen.getAllByText('objective.png').length).toBeGreaterThan(0);
      expect(screen.getAllByText('step.webp').length).toBeGreaterThan(0);
      expect(screen.getAllByText('step-second.jpg').length).toBeGreaterThan(0);
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
      waitingReason: 'Agent requested human feedback.',
      attentionRequired: true,
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

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Intervention' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Intervention Monitor' })).toBeTruthy();
      expect(screen.getAllByText('Agent requested human feedback.').length).toBeGreaterThan(0);
      expect(screen.getByRole('heading', { name: 'Observation' })).toBeTruthy();
      expect(screen.getAllByText(/Pause requested\./).length).toBeGreaterThan(0);
      expect(screen.getByText(/Live logs are passive observation only/i)).toBeTruthy();
    });
  });

  it('renders a side-by-side run comparison for related executions', async () => {
    window.history.pushState({}, 'Runs Test', '/workflows/test-123/runs?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Comparison task',
      summary: 'Compare runs',
      status: 'completed',
      state: 'completed',
      rawState: 'completed',
      targetRuntime: 'codex_cli',
      model: 'gpt-5',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: { canEditForRerun: true },
      relatedRuns: [
        {
          workflowId: 'test-456',
          runId: '02-run',
          relationship: 'Comparison source',
          status: 'failed',
          targetRuntime: 'gemini_cli',
          model: 'gemini-2.5-pro',
          effort: 'high',
          href: '/workflows/test-456?source=temporal',
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

    const comparisonPayload: BootPayload = {
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

    renderWithClient(<WorkflowDetailPage payload={comparisonPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Run Comparison' })).toBeTruthy();
      expect(screen.getAllByText('current').length).toBeGreaterThan(0);
      expect(screen.getAllByText('test-456').length).toBeGreaterThan(0);
      expect(screen.getAllByText('gpt-5').length).toBeGreaterThan(0);
      expect(screen.getByText('Gemini CLI')).toBeTruthy();
      expect(screen.getByText('gemini-2.5-pro')).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Workflow actions' }));
      expect(screen.getByRole('menuitem', { name: 'Compare run' }).getAttribute('href')).toBe(
        taskCompareHref('test-123'),
      );
    });
  });

  it('MM-772 renders the runs subroute as a dedicated execution history view', async () => {
    window.history.pushState({}, 'Runs Test', '/workflows/test-123/runs?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'History task',
      summary: 'Audit history',
      status: 'completed',
      state: 'completed',
      rawState: 'completed',
      targetRuntime: 'codex_cli',
      model: 'gpt-5',
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: '2026-03-28T00:00:03Z',
      closeStatus: 'completed',
      actions: {},
      interventionAudit: [
        {
          action: 'send_message',
          transport: 'temporal_update',
          summary: 'Operator guidance recorded.',
          createdAt: '2026-03-28T00:00:02Z',
        },
      ],
      relatedRuns: [
        {
          workflowId: 'test-456',
          runId: '02-run',
          relationship: 'rerun',
          status: 'failed',
          href: '/workflows/test-456/runs?source=temporal',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: 'Execution History' })).toHaveLength(1);
      expect(screen.getByRole('link', { name: 'Runs' }).getAttribute('aria-current')).toBe('page');
      expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('href')).toBe('/workflows/test-123?source=temporal');
      expect(screen.getAllByText(/^Current Run ID:?$/).length).toBeGreaterThan(0);
      expect(screen.getAllByText('01-run').length).toBeGreaterThan(0);
      expect(screen.getAllByText('test-456').length).toBeGreaterThan(0);
      expect(screen.getAllByText('02-run').length).toBeGreaterThan(0);
      expect(screen.getByRole('heading', { name: 'Run Comparison' })).toBeTruthy();
    });
  });

  it('MM-772 renders duplicate related runs without duplicate React keys', async () => {
    window.history.pushState({}, 'Runs Test', '/workflows/test-123/runs?source=temporal');
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'History task',
      summary: 'Audit history',
      status: 'completed',
      state: 'completed',
      rawState: 'completed',
      targetRuntime: 'codex_cli',
      model: 'gpt-5',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
      relatedRuns: [
        {
          workflowId: 'duplicate-workflow',
          relationship: 'rerun',
          status: 'failed',
          href: '/workflows/duplicate-workflow/runs?source=temporal',
        },
        {
          workflowId: 'duplicate-workflow',
          relationship: 'rerun',
          status: 'completed',
          href: '/workflows/duplicate-workflow/runs?source=temporal',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getAllByText('duplicate-workflow').length).toBeGreaterThanOrEqual(2);
    });
    const duplicateKeyWarnings = consoleErrorSpy.mock.calls.filter(([message]) =>
      String(message).includes('Encountered two children with the same key'),
    );
    expect(duplicateKeyWarnings).toHaveLength(0);
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

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    const menu = await openWorkflowActionsMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Pause' }));

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

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    let menu = await openWorkflowActionsMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Send Message' }));
    fireEvent.change(screen.getByLabelText('Message'), {
      target: { value: 'Please use Provider Profiles.' },
    });
    confirmWorkflowDialog('Send message');

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

    menu = await openWorkflowActionsMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Reject' }));
    typeWorkflowConfirmation('REJECT');
    confirmWorkflowDialog('Reject workflow');

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

    renderWithClient(<WorkflowDetailPage payload={actionPayload} />);

    const menu = await openWorkflowActionsMenu();
    expect(within(menu).getByRole('menuitem', { name: 'Cancel' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Skip Dependency Wait' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Bypass Dependencies' })).toBeNull();
  });

  it('renders a Session Continuity panel for Codex managed-session agent runs', async () => {
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
      agentRunId: 'wf-task-1',
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
            agent_run_id: 'wf-task-1',
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

    renderWithClient(<WorkflowDetailPage payload={codexPayload} />);

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
      agentRunId: 'wf-task-1',
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
            agent_run_id: 'wf-task-1',
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

    renderWithClient(<WorkflowDetailPage payload={codexPayload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/timeline shows what happened/i)).toBeTruthy();
      expect(screen.getByText(/durable evidence and drill-down/i)).toBeTruthy();
    });
  });

  it('routes Session Continuity follow-up and reset controls through the agent-run session control API', async () => {
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
      agentRunId: 'wf-task-1',
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
              agent_run_id: 'wf-task-1',
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
            agent_run_id: 'wf-task-1',
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

    renderWithClient(<WorkflowDetailPage payload={codexPayload} />);

    fireEvent.change(await screen.findByLabelText('Follow-up message'), {
      target: { value: 'Continue with the existing session.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send follow-up' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/agent-runs/wf-task-1/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control',
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
        '/api/agent-runs/wf-task-1/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control',
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
      agentRunId: 'wf-task-1',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {
        canCancel: true,
      },
    };

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
            agent_run_id: 'wf-task-1',
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

    renderWithClient(<WorkflowDetailPage payload={codexPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Cancel Execution' }));
    confirmWorkflowDialog('Cancel workflow');

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
  });

  it('keeps polling session continuity until a projection or terminal state exists', () => {
    expect(getSessionProjectionRefetchInterval(false, false, false)).toBe(5000);
    expect(getSessionProjectionRefetchInterval(false, true, false)).toBe(false);
    expect(getSessionProjectionRefetchInterval(false, false, true)).toBe(false);
    expect(getSessionProjectionRefetchInterval(true, false, false)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// LiveLogsPanel — full lifecycle tests via WorkflowDetailPage
// ---------------------------------------------------------------------------

describe('LiveLogsPanel', () => {
  const mockPayload: BootPayload = { page: 'workflow-detail', apiBase: '/api' };
  const fastPollPayload: BootPayload = {
    page: 'workflow-detail',
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
    agentRunId: '550e8400-e29b-41d4-a716-446655440000',
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
    page: 'workflow-detail',
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
    page: 'workflow-detail',
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
    page: 'workflow-detail',
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
    page: 'workflow-detail',
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
    page: 'workflow-detail',
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
    page: 'workflow-detail',
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
    window.history.pushState({}, 'Test', '/workflows/wf-1/steps?source=temporal');
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
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => expect(screen.getByText(/artifact line 1/)).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/artifact line 2/)).toBeTruthy());
    await waitFor(() => {
      expect(document.querySelectorAll('[data-stream]').length).toBe(2);
    });
  });

  it('appends log_chunk text from SSE after artifact tail is shown', async () => {
    mockFetchSequence(activeExecution, activeSummary, 'first from artifact\n');
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
      renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => expect(screen.getByText(/Stream ended/)).toBeTruthy(), { timeout: 5000 });
    expect(MockEventSource.instances.length).toBe(0);
  });

  it('does not create EventSource when supportsLiveStreaming is false', async () => {
    mockFetchSequence(activeExecution, noStreamSummary, 'artifact-only content\n');
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);
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
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);
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

    renderWithClient(<WorkflowDetailPage payload={sessionTimelinePayload} />);
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

    renderWithClient(<WorkflowDetailPage payload={sessionTimelinePayload} />);
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

    renderWithClient(<WorkflowDetailPage payload={sessionTimelinePayload} />);
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
    renderWithClient(<WorkflowDetailPage payload={legacyLiveLogsPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/legacy fallback line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-legacy-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-timeline-viewer')).toBeNull();
  });

  it('uses the legacy line viewer when the session timeline feature flag is absent', async () => {
    mockFetchSequence(activeExecution, activeSummary, 'legacy fallback line\n');
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/legacy fallback line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-legacy-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-timeline-viewer')).toBeNull();
  });

  it('enables the session timeline for codex managed runs when rollout is codex_managed', async () => {
    mockFetchSequence(codexExecution, activeSummary, 'codex rollout line\n');
    renderWithClient(<WorkflowDetailPage payload={codexManagedRolloutPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/codex rollout line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-timeline-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-legacy-viewer')).toBeNull();
  });

  it('keeps non-codex runs on the legacy viewer when rollout is codex_managed', async () => {
    mockFetchSequence(geminiExecution, activeSummary, 'gemini rollout line\n');
    renderWithClient(<WorkflowDetailPage payload={codexManagedRolloutPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/gemini rollout line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-legacy-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-timeline-viewer')).toBeNull();
  });

  it('enables the session timeline for managed runs when rollout is all_managed', async () => {
    mockFetchSequence(geminiExecution, activeSummary, 'all managed line\n');
    renderWithClient(<WorkflowDetailPage payload={allManagedRolloutPayload} />);

    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByText(/all managed line/)).toBeTruthy();
    });

    expect(screen.getByTestId('live-logs-timeline-viewer')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-legacy-viewer')).toBeNull();
  });

  it('prefers the legacy viewer when rollout is off even if the boolean flag is true', async () => {
    mockFetchSequence(codexExecution, activeSummary, 'rollout off line\n');
    renderWithClient(<WorkflowDetailPage payload={rolloutOffPayload} />);

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

    renderWithClient(<WorkflowDetailPage payload={sessionTimelinePayload} />);
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

    renderWithClient(<WorkflowDetailPage payload={structuredHistoryDisabledPayload} />);
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

    renderWithClient(<WorkflowDetailPage payload={sessionTimelinePayload} />);
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

    renderWithClient(<WorkflowDetailPage payload={sessionTimelinePayload} />);
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
          agentRunId: '123e4567-e89b-12d3-a456-426614174000',
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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
          agentRunId: 'mock-uuid-1',
          source: 'temporal',
          title: 'Mock task',
          summary: 'Mock summary',
          state: 'succeeded',
          status: 'completed',
          createdAt: '2026-03-28T00:00:00Z',
        }),
      } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
    expect(downloadLink.href).toMatch(/\/agent-runs\/mock-uuid-1\/logs\/merged$/);
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
          agentRunId: 'mock-uuid-1',
          source: 'temporal',
          title: 'Mock task',
          summary: 'Mock summary',
          state: 'succeeded',
          status: 'completed',
          createdAt: '2026-03-28T00:00:00Z',
        }),
      } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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

  it('polls execution detail until agentRunId appears and then attaches observability panels', async () => {
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
                agentRunId: undefined,
                updatedAt: '2026-03-28T00:00:00Z',
              }
            : activeExecution,
      } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={fastPollPayload} />);

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
          agentRunId: undefined,
          updatedAt: '2026-03-28T00:00:02Z',
        }),
      } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
          agentRunId: undefined,
          updatedAt: '2026-03-28T00:00:02Z',
        }),
      } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

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
