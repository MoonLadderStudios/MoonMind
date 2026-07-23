import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, act, fireEvent, within, cleanup } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import { EXECUTING_STATUS_PILL_TRACEABILITY } from '../utils/executionStatusPillClasses';
import {
  expandRouteTemplate,
  getSessionCapabilityRefetchInterval,
  getSessionProjectionRefetchInterval,
  normalizeObservabilityEvent,
  parseObservabilityEventsResponse,
  WORKFLOW_SIDEBAR_ANIMATED_RESTART_MS,
  WORKFLOW_SIDEBAR_ROUTE_ICON_ANIMATION_MS,
  WorkflowDetailEntrypoint,
  WorkflowDetailPage,
  workflowDetailQueryOptions,
  workflowEvidenceStaleTime,
} from './workflow-detail';

describe('bridge projection response contract', () => {
  it('fails visibly for an unknown page schema version', () => {
    expect(() =>
      parseObservabilityEventsResponse({
        schemaVersion: 'moonmind.bridge-session-events-page.v2',
        bridgeSessionId: 'brs-1',
        items: [],
        after: 0,
        nextCursor: null,
        hasMore: false,
        terminal: false,
        latestSequence: 0,
      }),
    ).toThrow();
  });

  it('preserves bridge pagination metadata for page draining', () => {
    expect(
      parseObservabilityEventsResponse({
        schemaVersion: 'moonmind.bridge-session-events-page.v1',
        bridgeSessionId: 'brs-1',
        items: [],
        after: 0,
        nextCursor: '100',
        hasMore: true,
        terminal: false,
        latestSequence: 101,
      }),
    ).toMatchObject({ nextCursor: '100', hasMore: true });
  });

  it('preserves the authoritative terminal envelope', () => {
    expect(parseObservabilityEventsResponse({
      schemaVersion: 'moonmind.bridge-session-events-page.v1',
      bridgeSessionId: 'brs-failed',
      items: [],
      after: 0,
      nextCursor: null,
      hasMore: false,
      terminal: true,
      latestSequence: 0,
      terminalEnvelope: {
        schemaVersion: 'moonmind.bridge-session-terminal.v1',
        status: 'failed',
        failureClass: 'configuration_error',
        failureCode: 'profile_missing',
        summary: 'Provider profile is unavailable.',
        diagnosticsRef: 'artifact:diagnostics',
        cleanupState: 'completed',
      },
    })).toMatchObject({
      terminal: true,
      terminalEnvelope: {
        status: 'failed',
        failureClass: 'configuration_error',
        failureCode: 'profile_missing',
        diagnosticsRef: 'artifact:diagnostics',
      },
    });
  });
});
import {
  taskCompareHref,
  taskEditForRerunHref,
  taskEditHref,
} from '../lib/temporalTaskEditing';
import { WORKFLOW_LIST_RETURN_FOCUS_INTENT_KEY } from '../lib/workflowListContext';
import { navigateTo } from '../lib/navigation';
import { BootPayload } from '../boot/parseBootPayload';
import { MockInstance } from 'vitest';
import {
  readDashboardPreferences,
  updateDashboardPreferences,
} from '../utils/dashboardPreferences';
import { WorkflowListPage } from './workflow-list';

declare const __dirname: string;

type MockVirtuosoRow = { id: string };
type MockVirtuosoProps<Row = MockVirtuosoRow> = {
  data?: Row[];
  computeItemKey?: (index: number, row: Row) => string;
  itemContent: (index: number, row: Row) => ReactNode;
  initialItemCount?: number;
  followOutput?: (atBottom: boolean) => 'smooth' | false;
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

async function readDashboardCss(): Promise<string> {
  const { readFileSync } = await import('node:fs');
  const { resolve } = await import('node:path');
  return readFileSync(resolve(__dirname, '../styles/dashboard.css'), 'utf8');
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
            sessionResources: '/api/sessions/{sessionId}/resources',
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
        status: 'completed',
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
        status: 'executing',
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

  it('MM-1133 gives workflow detail query consumers one canonical query identity', () => {
    const shellOptions = workflowDetailQueryOptions({
      apiBase: '/api',
      workflowId: 'mm:detail',
      sourceTemporal: true,
      detailPoll: 2000,
    });
    const pageOptions = workflowDetailQueryOptions({
      apiBase: '/api',
      workflowId: 'mm:detail',
      sourceTemporal: true,
      detailPoll: 2000,
    });

    expect(shellOptions.queryKey).toEqual(pageOptions.queryKey);
    expect(shellOptions.queryKey).toEqual(['workflow-detail', 'mm%3Adetail', true]);
    expect(shellOptions.staleTime).toBe(2000);
  });

  it('MM-1133 applies explicit stale windows to workflow evidence queries', () => {
    expect(workflowEvidenceStaleTime({ isTerminal: false, detailPoll: 2000 })).toBe(5000);
    expect(workflowEvidenceStaleTime({ isTerminal: false, detailPoll: 8000 })).toBe(8000);
    expect(workflowEvidenceStaleTime({ isTerminal: true, detailPoll: 2000 })).toBe(5000);
  });

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
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    virtuosoPropsSpy.mockClear();
    window.history.pushState({}, 'Test', '/workflows/test-123/steps?source=temporal');
    window.sessionStorage.clear();
    window.localStorage.clear();
    mockDesktopViewport(true);
    fetchSpy = vi.spyOn(window, 'fetch');
    fetchSpy.mockClear();
    vi.mocked(navigateTo).mockReset();
    vi.mocked(navigateTo).mockImplementation((path: string) => {
      window.history.pushState({ moonmindDashboard: true }, '', path);
    });
  });

  async function openWorkflowActionsMenu(expectedItemName?: string) {
    fireEvent.click(await screen.findByRole('button', { name: 'Workflow actions' }));
    const menu = screen.getByRole('menu', { name: 'Workflow actions' });
    if (expectedItemName) {
      await within(menu).findByRole('menuitem', { name: expectedItemName });
    }
    return menu;
  }

  function confirmWorkflowDialog(name: string) {
    fireEvent.click(screen.getByRole('button', { name }));
  }

  function mockWorkflowDetailSubrouteFetch({
    stepsSnapshot = latestStepsSnapshot,
    relatedRuns,
  }: {
    stepsSnapshot?: typeof latestStepsSnapshot;
    relatedRuns?: Array<Record<string, unknown>>;
  } = {}) {
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
      relatedRuns: relatedRuns ?? [
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
          json: async () => stepsSnapshot,
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

  function mockWorkflowWorkspaceFetches({
    rows,
  }: {
    rows?: Array<Record<string, unknown>>;
  } = {}) {
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
            items: rows ?? [
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

  function mockWorkflowWorkspaceFetchesWithSelectedOutsideList() {
    const selectedExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'MM-999 selected workflow outside filters',
      summary: 'Workspace shell selected detail',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: 'detail-updated-marker',
      scheduledFor: 'scheduled-marker',
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
                workflowId: 'test-456',
                taskId: 'test-456',
                source: 'temporal',
                title: 'Filtered workflow',
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
        json: async () => selectedExecution,
      } as Response);
    });
  }

  function mockWorkflowWorkspaceSidebarFailure() {
    const selectedExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'MM-999 detail survives sidebar error',
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

    let sidebarAttempts = 0;
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/executions?')) {
        sidebarAttempts += 1;
        return Promise.resolve({
          ok: false,
          statusText: sidebarAttempts === 1 ? 'Service Unavailable' : 'Gateway Timeout',
          json: async () => ({}),
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
        json: async () => selectedExecution,
      } as Response);
    });
  }

  function mockWorkflowWorkspaceDetailFailure() {
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
                title: 'MM-1010 sidebar remains loaded',
                status: 'running',
                state: 'executing',
                rawState: 'executing',
                createdAt: '2026-04-09T00:00:00Z',
              },
            ],
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
        ok: false,
        statusText: 'Forbidden',
        json: async () => ({}),
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

  it('renders the workflow detail header status pill with motion enabled for an executing run', async () => {
    window.history.pushState({}, 'Header Pill Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    await screen.findByRole('heading', { name: 'Workflow Detail' });
    await waitFor(() => {
      expect(document.querySelector('.toolbar-identity-row [data-effect="shimmer-sweep"]')).toBeTruthy();
    });

    const pill = document.querySelector<HTMLElement>('.toolbar-identity-row [data-effect="shimmer-sweep"]');
    expect(pill?.dataset.state).toBe('executing');
    expect(pill?.className).toContain('status-running');
    expect(pill?.className).toContain('is-executing');
    expect(pill?.getAttribute('aria-label')).toBe('Executing');
    expect(pill?.querySelector('.status-letter-wave')?.getAttribute('data-label')).toBe('Executing');
    expect(pill?.querySelector('.status-letter-wave')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('canonicalizes a raw running header status to the executing shimmer treatment', async () => {
    window.history.pushState({}, 'Header Alias Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();
    const workspaceFetch = fetchSpy.getMockImplementation();
    if (!workspaceFetch) {
      throw new Error('Expected mockWorkflowWorkspaceFetches() to configure fetchSpy');
    }
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = (await workspaceFetch(input, init)) as Response;
      const url = String(input);
      if (url.includes('/executions/test-123') && !url.includes('/steps')) {
        const execution = await response.json();
        return {
          ok: true,
          json: async () => ({ ...execution, rawState: 'running', state: 'completed' }),
        } as Response;
      }
      return response;
    });

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    await screen.findByRole('heading', { name: 'Workflow Detail' });
    await waitFor(() => {
      expect(document.querySelector('.toolbar-identity-row [data-effect="shimmer-sweep"]')).toBeTruthy();
    });

    const pill = document.querySelector<HTMLElement>('.toolbar-identity-row [data-effect="shimmer-sweep"]');
    expect(pill?.dataset.state).toBe('executing');
    expect(pill?.getAttribute('aria-label')).toBe('Executing');
  });

  it('MM-1133 keeps the workspace sidebar list cache isolated from the workflow table cache', async () => {
    window.history.pushState({}, 'Workspace Cache Test', '/workflows?limit=25&source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    const payload: BootPayload = {
      page: 'workflow-list',
      apiBase: '/api',
      initialData: {
        dashboardConfig: {
          pollIntervalsMs: { detail: 60000, list: 60000 },
          features: {
            temporalDashboard: {
              listEnabled: true,
              workspaceShellEnabled: true,
            },
          },
        },
      },
    };
    const view = renderWithClient(<WorkflowListPage payload={payload} />);

    expect(await screen.findByRole('row', { name: /MM-997 selected workflow/i })).toBeTruthy();
    const matchingListCalls = () => fetchSpy.mock.calls.filter(
      ([input]) => String(input) === '/api/executions?source=temporal&pageSize=25',
    );
    expect(matchingListCalls()).toHaveLength(1);

    window.history.pushState({}, 'Workspace Cache Test', '/workflows/test-123?limit=25&source=temporal');
    view.rerender(
      <WorkflowDetailEntrypoint
        payload={{
          ...stepsPayload,
          initialData: {
            ...(stepsPayload.initialData as Record<string, unknown>),
            dashboardConfig: {
              ...((stepsPayload.initialData as { dashboardConfig: Record<string, unknown> }).dashboardConfig),
              pollIntervalsMs: { detail: 60000, list: 60000 },
            },
          },
        }}
      />,
    );

    expect(await screen.findByRole('complementary', { name: 'Workflow navigation' })).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(matchingListCalls()).toHaveLength(2);
  });


  it('MM-1186 renders the Workflow adapter through the shared collection sidebar', async () => {
    window.history.pushState({}, 'Workspace Table Slice Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(sidebar.classList.contains('collection-sidebar')).toBe(true);
    const table = await within(sidebar).findByRole('table', { name: 'Workflow list table slice' });
    const header = within(table).getByRole('columnheader', { name: 'Workflow' });
    expect(header.closest('.workflow-workspace-sidebar-header-row')).toBeTruthy();
    expect(within(header).queryByRole('link')).toBeNull();

    const selected = within(table).getByRole('link', { name: /MM-997 selected workflow/i });
    const another = within(table).getByRole('link', { name: /Another workflow/i });
    expect(header.compareDocumentPosition(selected) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(selected.getAttribute('aria-current')).toBe('page');
    expect(another.getAttribute('href')).toBe('/workflows/test-456?source=temporal');

    const workflowFilter = within(header).getByRole('button', { name: 'Workflow sidebar filter. No filter applied.' });
    const workflowHeader = workflowFilter.closest('.workflow-list-column-header');
    expect(workflowHeader?.querySelector('.workflow-workspace-sidebar-header-title')?.textContent).toContain('Workflow');

    fireEvent.click(workflowFilter);
    fireEvent.change(screen.getByLabelText('Workflow sidebar filter value'), {
      target: { value: 'Another' },
    });

    expect(within(table).queryByRole('link', { name: /MM-997 selected workflow/i })).toBeNull();
    expect(within(table).getByRole('link', { name: /Another workflow/i })).toBeTruthy();
  });

  it('MM-1116 preserves the sidebar table-slice header for loading, error, and empty states', async () => {
    mockDesktopViewport(true);

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/executions?')) {
        return new Promise(() => {});
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
        json: async () => ({
          taskId: 'test-123',
          workflowId: 'test-123',
          namespace: 'default',
          temporalRunId: '02-run',
          runId: '02-run',
          stepsHref: '/api/executions/test-123/steps',
          source: 'temporal',
          workflowType: 'MoonMind.UserWorkflow',
          title: 'Loading state detail',
          status: 'running',
          state: 'executing',
          rawState: 'executing',
          temporalStatus: 'running',
          createdAt: '2026-04-09T00:00:00Z',
          updatedAt: '2026-04-09T00:00:04Z',
          actions: {},
          relatedRuns: [],
        }),
      } as Response);
    });

    window.history.pushState({}, 'Workspace Loading Slice Test', '/workflows/test-123?source=temporal');
    const loadingRender = renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);
    const loadingSidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    const loadingTable = await within(loadingSidebar).findByRole('table', { name: 'Workflow list table slice' });
    expect(within(loadingTable).getByRole('columnheader', { name: 'Workflow' })).toBeTruthy();
    expect(within(loadingTable).getByText('Loading workflows...')).toBeTruthy();
    loadingRender.unmount();

    mockWorkflowWorkspaceSidebarFailure();
    window.history.pushState({}, 'Workspace Error Slice Test', '/workflows/test-123?source=temporal');
    const errorRender = renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);
    const errorSidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    const errorTable = await within(errorSidebar).findByRole('table', { name: 'Workflow list table slice' });
    expect(within(errorTable).getByRole('columnheader', { name: 'Workflow' })).toBeTruthy();
    expect(await within(errorTable).findByText('Workflow navigation is unavailable.')).toBeTruthy();
    errorRender.unmount();

    mockWorkflowWorkspaceFetches({ rows: [] });
    window.history.pushState({}, 'Workspace Empty Slice Test', '/workflows/test-123?source=temporal&stateIn=failed');
    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);
    const emptySidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    const emptyTable = await within(emptySidebar).findByRole('table', { name: 'Workflow list table slice' });
    expect(within(emptyTable).getByRole('columnheader', { name: 'Workflow' })).toBeTruthy();
    expect(await within(emptyTable).findByText('No workflows match the current list filters.')).toBeTruthy();
  });

  it('MM-999 pins the selected workflow above sidebar rows when it is outside the filtered result', async () => {
    window.history.pushState({}, 'Workspace Pinned Current Test', '/workflows/test-123?source=temporal&stateIn=completed');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetchesWithSelectedOutsideList();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    const pinnedGroup = await within(sidebar).findByRole('rowgroup', { name: 'Current workflow' });
    const pinned = within(pinnedGroup).getByRole('link', { name: /MM-999 selected workflow outside filters/i });
    expect(pinned.getAttribute('aria-current')).toBe('page');
    expect(pinned.getAttribute('data-pinned')).toBe('true');
    expect(within(pinned).getByLabelText('Status: Executing')).toBeTruthy();
    expect(within(sidebar).getByRole('link', { name: /Filtered workflow/i }).getAttribute('aria-current')).toBeNull();
    expect(screen.getByRole('main', { name: 'Workflow detail' })).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
  });

  it('MM-1113 keeps authorized remembered workflows outside filters in the current group only', async () => {
    window.history.pushState({}, 'Workspace Remembered Current Test', '/workflows/test-123?source=temporal&stateIn=completed');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetchesWithSelectedOutsideList();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    const pinnedGroup = await within(sidebar).findByRole('rowgroup', { name: 'Current workflow' });
    const filterMatchingList = within(sidebar).getByRole('rowgroup', { name: 'Workflow navigation list' });
    expect(within(pinnedGroup).getByRole('link', { name: /MM-999 selected workflow outside filters/i })).toBeTruthy();
    expect(within(filterMatchingList).queryByRole('link', { name: /MM-999 selected workflow outside filters/i })).toBeNull();
    expect(within(filterMatchingList).getByRole('link', { name: /Filtered workflow/i })).toBeTruthy();
  });

  it('MM-1113 renders only authorized sidebar rows returned by the list endpoint', async () => {
    window.history.pushState({}, 'Workspace Authorized Sidebar Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches({
      rows: [
        {
          workflowId: 'test-123',
          taskId: 'test-123',
          source: 'temporal',
          title: 'Authorized sidebar workflow',
          status: 'running',
          state: 'executing',
          rawState: 'executing',
          createdAt: '2026-04-09T00:00:00Z',
        },
      ],
    });

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(await within(sidebar).findByRole('link', { name: /Authorized sidebar workflow/i })).toBeTruthy();
    expect(within(sidebar).queryByText(/unauthorized/i)).toBeNull();
    expect(within(sidebar).queryByRole('link', { name: /unauthorized/i })).toBeNull();
  });

  it('MM-1064 renders compact sidebar status icons for canonical lifecycle states', async () => {
    window.history.pushState({}, 'Workspace Sidebar Icons Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches({
      rows: [
      ['scheduled', 'Scheduled workflow'],
        ['initializing', 'Initializing workflow'],
        ['running', 'Running workflow'],
        ['waiting_on_dependencies', 'Dependency wait workflow'],
        ['planning', 'Planning workflow'],
        ['awaiting_slot', 'Slot wait workflow'],
        ['executing', 'Executing workflow'],
        ['proposals', 'Proposals workflow'],
        ['awaiting_external', 'External wait workflow'],
        ['finalizing', 'Finalizing workflow'],
        ['no_commit', 'No commit workflow'],
        ['completed', 'Completed workflow'],
        ['failed', 'Failed workflow'],
        ['canceled', 'Canceled workflow'],
        ['constructor', 'Unknown prototype workflow'],
      ].map(([rawState, title]) => ({
        workflowId: `test-${rawState}`,
        taskId: `test-${rawState}`,
        source: 'temporal',
        title,
        status: rawState,
        state: rawState,
        rawState,
        createdAt: '2026-04-09T00:00:00Z',
      })),
    });

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    const expectedIcons = [
      ['Scheduled workflow', 'Status: Scheduled', 'status-scheduled', 'lucide-moon'],
      ['Initializing workflow', 'Status: Initializing', 'status-initializing', null],
      ['Running workflow', 'Status: Running', 'status-running', null],
      ['Dependency wait workflow', 'Status: Awaiting dependencies', 'status-awaiting-dependencies', 'lucide-link'],
      ['Planning workflow', 'Status: Planning', 'status-planning', null],
      ['Slot wait workflow', 'Status: Awaiting slot', 'status-awaiting-slot', 'lucide-hourglass'],
      ['Executing workflow', 'Status: Executing', 'status-running', null],
      ['Proposals workflow', 'Status: Proposals', 'status-running', 'lucide-lightbulb'],
      ['External wait workflow', 'Status: Awaiting external', 'status-awaiting-external', 'lucide-hand'],
      ['Finalizing workflow', 'Status: Finalizing', 'status-finalizing', null],
      ['No commit workflow', 'Status: No commit', 'status-no-commit', 'lucide-minus'],
      ['Completed workflow', 'Status: Completed', 'status-completed', 'lucide-check'],
      ['Failed workflow', 'Status: Failed', 'status-failed', 'lucide-x'],
      ['Canceled workflow', 'Status: Canceled', 'status-canceled', 'lucide-ban'],
      ['Unknown prototype workflow', 'Status: constructor', 'status-neutral', 'lucide-play'],
    ] as const;

    for (const [title, ariaLabel, statusClass, iconClass] of expectedIcons) {
      const row = await within(sidebar).findByRole('link', { name: new RegExp(title, 'i') });
      const iconContainer = within(row).getByLabelText(ariaLabel);
      expect(iconContainer.classList.contains('workflow-workspace-sidebar-status-icon')).toBe(true);
      expect(iconContainer.classList.contains(statusClass)).toBe(true);
      expect(iconContainer.getAttribute('data-effect')).toBeNull();
      if (iconClass) {
        expect(iconContainer.querySelector(`svg.${iconClass}`)).toBeTruthy();
      } else {
        expect(iconContainer.querySelector('svg')).toBeTruthy();
      }
      expect(within(row).queryByText(ariaLabel.replace('Status: ', ''))).toBeNull();
    }
  });

  it('MM-1108 lets the planning and finalizing RouteIcon animations finish before replaying', () => {
    expect(WORKFLOW_SIDEBAR_ANIMATED_RESTART_MS.planning).toBeGreaterThan(
      WORKFLOW_SIDEBAR_ROUTE_ICON_ANIMATION_MS,
    );
    expect(WORKFLOW_SIDEBAR_ANIMATED_RESTART_MS.finalizing).toBe(
      WORKFLOW_SIDEBAR_ANIMATED_RESTART_MS.planning,
    );
  });

  it('MM-999 does not show a pinned current row when the selected workflow is in the normal sidebar list', async () => {
    window.history.pushState({}, 'Workspace No Pinned Current Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(within(sidebar).queryByRole('rowgroup', { name: 'Current workflow' })).toBeNull();
    expect((await within(sidebar).findByRole('link', { name: /MM-997 selected workflow/i })).getAttribute('aria-current')).toBe(
      'page',
    );
  });

  it('MM-1010 keeps an empty filtered sidebar state, pinned current workflow, and detail content independent (MM-975)', async () => {
    window.history.pushState({}, 'Workspace Empty Filter Test', '/workflows/test-123?source=temporal&stateIn=failed');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches({ rows: [] });

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(await within(sidebar).findByText('No workflows match the current list filters.')).toBeTruthy();
    const pinnedGroup = within(sidebar).getByRole('rowgroup', { name: 'Current workflow' });
    const pinned = within(pinnedGroup).getByRole('link', { name: /MM-997 selected workflow/i });
    expect(pinned.getAttribute('aria-current')).toBe('page');
    expect(pinned.getAttribute('data-pinned')).toBe('true');
    expect(within(sidebar).queryByRole('link', { name: 'Expand to full list' })).toBeNull();
    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).toBe('/api/executions?source=temporal&pageSize=25&stateIn=failed');
  });

  it('MM-999 keeps detail visible and retries only sidebar data after a recoverable sidebar error', async () => {
    window.history.pushState({}, 'Workspace Sidebar Error Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceSidebarFailure();

    renderWithClient(
      <WorkflowDetailEntrypoint
        payload={{
          ...stepsPayload,
          initialData: {
            dashboardConfig: {
              ...((stepsPayload.initialData as { dashboardConfig: Record<string, unknown> }).dashboardConfig),
              pollIntervalsMs: { detail: 60000, list: 60000 },
            },
          },
        }}
      />,
    );

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(await within(sidebar).findByText('Workflow navigation is unavailable.')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    const detailCallsBeforeRetry = fetchSpy.mock.calls.filter(
      ([input]) => String(input) === '/api/executions/test-123?source=temporal',
    ).length;

    fireEvent.click(within(sidebar).getByRole('button', { name: 'Retry' }));

    await waitFor(() => {
      const sidebarFetches = fetchSpy.mock.calls.filter(([url]) => String(url).startsWith('/api/executions?'));
      expect(sidebarFetches.length).toBeGreaterThanOrEqual(2);
    });
    expect(screen.getByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(
      fetchSpy.mock.calls.filter(
        ([input]) => String(input) === '/api/executions/test-123?source=temporal',
      ).length,
    ).toBe(detailCallsBeforeRetry);
  });

  it('MM-1010 keeps a loaded desktop sidebar visible when the selected detail request fails (MM-975)', async () => {
    window.history.pushState({}, 'Workspace Detail Failure Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceDetailFailure();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    const active = await within(sidebar).findByRole('link', { name: /MM-1010 sidebar remains loaded/i });
    expect(active.getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('main', { name: 'Workflow detail' })).toBeTruthy();
    expect(await screen.findByText('Failed to fetch workflow: Forbidden')).toBeTruthy();
    expect(within(sidebar).queryByText('Workflow navigation is unavailable.')).toBeNull();
  });

  it('MM-997 translates workspace sidebar limit state to the executions API page size', async () => {
    window.history.pushState(
      {},
      'Workspace Query Test',
      '/workflows/test-123?source=temporal&limit=10&nextPageToken=page-2&selectedWorkflowId=test-123&sort=status&unsafe=1',
    );
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    expect(await screen.findByRole('complementary', { name: 'Workflow navigation' })).toBeTruthy();
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).toBe(
      '/api/executions?source=temporal&pageSize=10&nextPageToken=page-2',
    );
  });

  it('MM-1002 keeps sidebar API and workflow links within allowlisted workflow context', async () => {
    window.history.pushState(
      {},
      'Workspace Sidebar Security Test',
      '/workflows/test-123?source=temporal&limit=10&nextPageToken=page-2&repoContains=moon%2Frepo&integration=jira&selectedWorkflowId=test-123&sort=status&token=secret&unsafe=1',
    );
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).toBe(
      '/api/executions?source=temporal&pageSize=10&nextPageToken=page-2&repoContains=moon%2Frepo&integration=jira',
    );
    const anotherWorkflow = await within(sidebar).findByRole('link', { name: /Another workflow/i });
    expect(anotherWorkflow.getAttribute('href')).toBe(
      '/workflows/test-456?source=temporal&limit=10&nextPageToken=page-2&repoContains=moon%2Frepo&integration=jira',
    );
    expect(within(sidebar).queryByRole('link', { name: 'Expand to full list' })).toBeNull();
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).not.toContain('selectedWorkflowId=');
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).not.toContain('sort=');
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).not.toContain('token=');
  });

  it('preserves API-style pageSize state when fetching the workspace sidebar', async () => {
    window.history.pushState(
      {},
      'Workspace Page Size Test',
      '/workflows/test-123?source=temporal&pageSize=100&nextPageToken=page-2&selectedWorkflowId=test-123&sort=status&unsafe=1',
    );
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    expect(await screen.findByRole('complementary', { name: 'Workflow navigation' })).toBeTruthy();
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).toBe(
      '/api/executions?source=temporal&pageSize=100&nextPageToken=page-2',
    );
  });

  it('renders hidden mode from the shared display model without changing the detail route', async () => {
    window.history.pushState({}, 'Workspace Collapse Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    const { container } = renderWithClient(
      <WorkflowDetailEntrypoint
        payload={{
          ...stepsPayload,
          initialData: {
            ...(stepsPayload.initialData as Record<string, unknown>),
            workflowListDisplayMode: 'hidden',
          },
        }}
      />,
    );

    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();
    expect(container.querySelector('.workflow-workspace-shell')?.getAttribute('data-workflow-list-display-mode')).toBe(
      'hidden',
    );
    expect(window.location.pathname).toBe('/workflows/test-123');
    expect(screen.getByRole('main', { name: 'Workflow detail' })).toBeTruthy();
  });

  it('defaults desktop detail routes to sidebar unless the previous collapse preference requests hidden mode', async () => {
    window.history.pushState({}, 'Workspace Reload Default Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    const firstRender = renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    expect(await screen.findByRole('complementary', { name: 'Workflow navigation' })).toBeTruthy();
    expect(firstRender.container.querySelector('.workflow-workspace-shell')?.getAttribute('data-workflow-list-display-mode')).toBe(
      'sidebar',
    );

    cleanup();
    window.localStorage.clear();
    updateDashboardPreferences({ workflowListDisplayMode: 'hidden' });

    const secondRender = renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();
    expect(secondRender.container.querySelector('.workflow-workspace-shell')?.getAttribute('data-workflow-list-display-mode')).toBe(
      'hidden',
    );
  });

  it('shared sidebar mode overrides a persisted collapse preference without refetching selected detail data', async () => {
    window.history.pushState({}, 'Workspace Reopen Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();
    updateDashboardPreferences({ workflowListDisplayMode: 'hidden' });

    renderWithClient(
      <WorkflowDetailEntrypoint
        payload={{
          ...stepsPayload,
          initialData: {
            workflowListDisplayMode: 'sidebar',
            dashboardConfig: {
              ...((stepsPayload.initialData as { dashboardConfig: Record<string, unknown> }).dashboardConfig),
              pollIntervalsMs: { detail: 60000, list: 60000 },
            },
          },
        }}
      />,
    );

    await screen.findByRole('heading', { name: 'Workflow Detail' });
    const detailCallsBeforeOpen = fetchSpy.mock.calls.filter(
      ([input]) => String(input) === '/api/executions/test-123?source=temporal',
    ).length;

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(sidebar).toBeTruthy();
    expect(readDashboardPreferences().workflowListDisplayMode).toBe('hidden');
    expect(
      fetchSpy.mock.calls.filter(
        ([input]) => String(input) === '/api/executions/test-123?source=temporal',
      ).length,
    ).toBe(detailCallsBeforeOpen);
  });

  it('MM-1000 keeps persisted collapsed state out of mobile standalone detail routing', async () => {
    window.history.pushState({}, 'Workspace Mobile Collapse Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(false);
    mockWorkflowWorkspaceFetches();
    updateDashboardPreferences({ workflowListDisplayMode: 'hidden' });

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Open workflow sidebar' })).toBeNull();
    expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();
  });

  it('replaces workspace sidebar full-list navigation with the shared mode model', async () => {
    window.history.pushState(
      {},
      'Workspace Expand Test',
      '/workflows/test-123?source=temporal&stateIn=completed&repoContains=moon%2Frepo&limit=10&nextPageToken=page-2&sort=status&selectedWorkflowId=test-123&unsafe=1',
    );
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(within(sidebar).queryByRole('link', { name: 'Expand to full list' })).toBeNull();
    expect(within(sidebar).queryByRole('button', { name: 'Close sidebar' })).toBeNull();
  });

  it('MM-1008 does not render separate covered-page sidebar controls', async () => {
    window.history.pushState({}, 'Workspace Controls Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(within(sidebar).queryByRole('button', { name: 'Close sidebar' })).toBeNull();
    expect(within(sidebar).queryByRole('button', { name: 'Open workflow sidebar' })).toBeNull();
    expect(within(sidebar).queryByRole('link', { name: 'Expand to full list' })).toBeNull();

    const dashboardCss = await readDashboardCss();
    expect(dashboardCss).not.toMatch(/\.workflow-workspace-expand-list,[\s\S]*?\.workflow-workspace-close-sidebar/);
  });

  it('MM-1002 renders sidebar titles and statuses as React text', async () => {
    window.history.pushState({}, 'Workspace Sidebar Text Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches({
      rows: [
        {
          workflowId: 'test-123',
          taskId: 'test-123',
          title: '<img src=x onerror=alert(1)>',
          status: '<script>alert(1)</script>',
          state: '<script>alert(1)</script>',
          rawState: '<script>alert(1)</script>',
          repository: '<b>owner/repo</b>',
          targetRuntime: 'codex_cli',
          createdAt: '2026-04-09T00:00:00Z',
        },
      ],
    });

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    // The sidebar row is now just the title and a status icon; untrusted fields
    // must not become live DOM nodes.
    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(await within(sidebar).findByText('<img src=x onerror=alert(1)>')).toBeTruthy();
    const statusIcon = within(sidebar).getByLabelText('Status: <script>alert(1)</script>');
    expect(statusIcon.getAttribute('title')).toBe('<script>alert(1)</script>');
    expect(within(sidebar).queryByRole('img')).toBeNull();
    expect(sidebar.querySelector('script')).toBeNull();
  });

  it('MM-1005 leaves table expansion to the shared masthead mode model', async () => {
    window.history.pushState({}, 'Workspace Plain Expand Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();

    renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    const sidebar = await screen.findByRole('complementary', { name: 'Workflow navigation' });
    expect(within(sidebar).queryByRole('link', { name: 'Expand to full list' })).toBeNull();
    expect(window.sessionStorage.getItem(WORKFLOW_LIST_RETURN_FOCUS_INTENT_KEY)).toBeNull();
  });

  it('keeps desktop detail positioning stable when the workspace sidebar is collapsed', async () => {
    window.history.pushState({}, 'Workspace Motion Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(true);
    mockWorkflowWorkspaceFetches();
    updateDashboardPreferences({ workflowListDisplayMode: 'hidden' });

    const { container } = renderWithClient(<WorkflowDetailEntrypoint payload={stepsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Open workflow sidebar' })).toBeNull();
    expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();
    expect(container.querySelector('.workflow-workspace-shell')?.getAttribute('data-sidebar-collapsed')).toBe(
      'true',
    );

    const dashboardCss = await readDashboardCss();
    expect(dashboardCss).toMatch(
      /\.workflow-workspace-shell,\s*\.collection-workspace--edge-rail\s*\{[^}]*grid-template-columns:\s*\[rail-start\] minmax\(0,\s*1fr\)\s*\[content-start\] min\(var\(--mm-content-max\),\s*calc\(100% - 2rem\)\)\s*\[content-end\] minmax\(0,\s*1fr\);/,
    );
    expect(dashboardCss).toMatch(
      /@media \(max-width:\s*114rem\) and \(min-width:\s*768px\)\s*\{[\s\S]*\.workflow-workspace-shell\[data-sidebar-collapsed="true"\],\s*\.collection-workspace--edge-rail\[data-sidebar-collapsed="true"\]\s*\{[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\);/,
    );
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*\.workflow-workspace-shell,[\s\S]*\.workflow-workspace-detail[\s\S]*transition:\s*none !important;[\s\S]*animation:\s*none !important;[\s\S]*transform:\s*none !important;/,
    );
    expect(dashboardCss).toMatch(
      /\.workflow-workspace-detail\s*\{[^}]*max-width:\s*66rem;/,
    );
    expect(dashboardCss).toMatch(
      /\.workflow-workspace-detail\s*\{[^}]*padding-top:\s*0\.85rem;/,
    );
    expect(dashboardCss).toMatch(
      /@media \(max-width:\s*114rem\) and \(min-width:\s*768px\)\s*\{[\s\S]*\.workflow-workspace-shell\[data-sidebar-collapsed="true"\] \.workflow-workspace-detail,[\s\S]*\.workflow-start-workspace\[data-sidebar-collapsed="true"\] \.workflow-start-primary\s*\{[\s\S]*grid-column:\s*1 \/ -1;/,
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

  it('MM-1001 keeps mobile direct detail links standalone with no sidebar control leakage', async () => {
    window.history.pushState({}, 'Workspace Mobile Direct Detail Test', '/workflows/test-123?source=temporal');
    mockDesktopViewport(false);
    mockWorkflowWorkspaceFetches();

    renderWithClient(
      <WorkflowDetailEntrypoint
        payload={{
          ...stepsPayload,
          initialData: {
            dashboardConfig: {
              ...((stepsPayload.initialData as { dashboardConfig: unknown }).dashboardConfig as Record<string, unknown>),
              features: {
                temporalDashboard: {
                  workspaceShellEnabled: true,
                  listEnabled: true,
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Back to workflows' })).toBeNull();
    expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Close sidebar' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Open workflow sidebar' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Expand to full list' })).toBeNull();
    expect(document.querySelector('.workflow-workspace-shell')).toBeNull();
    expect(document.querySelector('.workflow-workspace-sidebar')).toBeNull();
    expect(lastFetchUrl(fetchSpy, '/api/executions?')).toBeUndefined();
  });

  it('syncs the active detail tab when the workflow URL changes under a stable parent', async () => {
    window.history.pushState({}, 'Detail Tab Sync Test', '/workflows/test-123/steps?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    const rendered = renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Execution' }).getAttribute('aria-current')).toBe('page');

    window.history.pushState({}, 'Detail Tab Sync Test', '/workflows/test-123/overview?source=temporal');
    rendered.rerender(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBe('page');
    });
    expect(screen.getByRole('link', { name: 'Execution' }).getAttribute('aria-current')).not.toBe('page');
  });

  it('MM-1094/MM-1105 renders checkpoint branches, evidence links, and policy blocked action states', async () => {
    window.history.pushState({}, 'Branch Explorer Test', '/workflows/test-123/steps?source=temporal');
    const stepsWithCheckpoint = {
      ...latestStepsSnapshot,
      steps: latestStepsSnapshot.steps.map((step) => (
        step.logicalStepId === 'apply'
          ? { ...step, stateCheckpointRef: 'artifact://checkpoint-apply' }
          : step
      )),
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
      title: 'MM-1094 branch workflow',
      summary: 'Branch explorer detail',
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
      if (url.endsWith('/checkpoint-branches/branch-a/turns')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [{
              branchTurnId: 'turn-a-1',
              branchId: 'branch-a',
              instructionRef: 'art-instructions-a',
              instructionDigest: 'sha256:a',
              sourceCheckpointRef: 'artifact://checkpoint-apply',
              createdStepExecutionId: 'step-head-a',
              stepExecutionManifestRef: 'art-manifest-a',
              idempotencyKey: 'idem-turn-a',
              status: 'passed',
              createdAt: '2026-04-09T00:01:00Z',
              updatedAt: '2026-04-09T00:02:00Z',
            }],
          }),
        } as Response);
      }
      if (url.endsWith('/checkpoint-branches')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [{
              branchId: 'branch-a',
              workflowId: 'test-123',
              rootWorkflowId: 'test-123',
              sourceRunId: '02-run',
              logicalStepId: 'apply',
              sourceExecutionOrdinal: 1,
              sourceCheckpointBoundary: 'after_execution',
              sourceCheckpointRef: 'artifact://checkpoint-apply',
              label: 'Try minimal API contract fix',
              state: 'promotable',
              branchKind: 'root',
              workspacePolicy: 'apply_previous_execution_diff_to_clean_baseline',
              runtimeContextPolicy: 'fresh_agent_run',
              gitRepository: 'MoonLadderStudios/MoonMind',
              gitBaseBranch: 'main',
              gitWorkBranch: 'mm/test-123/apply/branch-a',
              currentHeadStepExecutionId: 'step-head-a',
              currentHeadCheckpointRef: 'artifact://checkpoint-head-a',
              artifactRefs: { summary: 'artifact://branch-summary-a' },
              publishStatus: 'unpublished',
              createdAt: '2026-04-09T00:01:00Z',
              updatedAt: '2026-04-09T00:02:00Z',
            }],
          }),
        } as Response);
      }
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => stepsWithCheckpoint } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Branch Explorer' })).toBeTruthy();
    expect(await screen.findByText('Try minimal API contract fix')).toBeTruthy();
    expect(await screen.findByRole('list', { name: 'Branch turns' })).toBeTruthy();
    expect(screen.getByText('artifact://branch-summary-a')).toBeTruthy();
    expect(screen.getByText('artifact://checkpoint-head-a')).toBeTruthy();
    expect(screen.getByText('Create branch unavailable: Branch actions are disabled by workflow policy.')).toBeTruthy();
    expect(screen.getByText('Branch action unavailable: Branch actions are disabled by workflow policy.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Create branch from checkpoint' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Create branch from checkpoint' }).getAttribute('title')).toBe(
      'Branch actions are disabled by workflow policy.',
    );
    expect((screen.getByRole('button', { name: 'Continue branch' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Continue branch' }).getAttribute('title')).toBe(
      'Branch actions are disabled by workflow policy.',
    );
  });

  it('MM-1094 submits branch creation from the safety preview checkpoint', async () => {
    window.history.pushState({}, 'Branch Create Test', '/workflows/test-123/steps?source=temporal');
    const stepsWithCheckpoint = {
      ...latestStepsSnapshot,
      steps: latestStepsSnapshot.steps.map((step) => (
        step.logicalStepId === 'apply'
          ? { ...step, stateCheckpointRef: 'artifact://checkpoint-apply' }
          : step
      )),
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
      title: 'MM-1094 branch workflow',
      summary: 'Branch explorer detail',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
      relatedRuns: [],
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/checkpoint-branches') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            branchId: 'branch-created',
            workflowId: 'test-123',
            rootWorkflowId: 'test-123',
            sourceRunId: '02-run',
            logicalStepId: 'apply',
            sourceExecutionOrdinal: 1,
            sourceCheckpointBoundary: 'after_execution',
            sourceCheckpointRef: 'artifact://checkpoint-apply',
            label: 'Checkpoint branch',
            state: 'created',
            branchKind: 'root',
            workspacePolicy: 'apply_previous_execution_diff_to_clean_baseline',
            runtimeContextPolicy: 'fresh_agent_run',
            artifactRefs: {},
            createdAt: '2026-04-09T00:01:00Z',
            updatedAt: '2026-04-09T00:01:00Z',
          }),
        } as Response);
      }
      if (url.endsWith('/checkpoint-branches')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => stepsWithCheckpoint } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create branch from checkpoint' }));

    await waitFor(() => {
      const postCall = fetchSpy.mock.calls.find(([url, init]) => (
        String(url).endsWith('/checkpoint-branches') && (init as RequestInit | undefined)?.method === 'POST'
      ));
      expect(postCall).toBeTruthy();
      const body = JSON.parse(String((postCall?.[1] as RequestInit).body));
      expect(body.source.checkpointRef).toBe('artifact://checkpoint-apply');
      expect(body.source.logicalStepId).toBe('apply');
      expect(body.workspacePolicy).toBe('apply_previous_execution_diff_to_clean_baseline');
      expect(body.runtimeContextPolicy).toBe('fresh_agent_run');
      expect(body.publishMode).toBe('none');
      expect(body.instructions.text).toContain('bounded alternative implementation');
      expect(body.idempotencyKey).toMatch(/^dashboard:create:test-123:apply:1:/);
    });
  });

  it('MM-1094 submits promotable branches with accepted promotion evidence', async () => {
    window.history.pushState({}, 'Branch Promote Test', '/workflows/test-123/steps?source=temporal');
    const stepsWithCheckpoint = {
      ...latestStepsSnapshot,
      steps: latestStepsSnapshot.steps.map((step) => (
        step.logicalStepId === 'apply'
          ? { ...step, stateCheckpointRef: 'artifact://checkpoint-apply' }
          : step
      )),
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
      title: 'MM-1094 branch workflow',
      summary: 'Branch explorer detail',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
      relatedRuns: [],
    };
    const branch = {
      branchId: 'branch-a',
      workflowId: 'test-123',
      rootWorkflowId: 'test-123',
      sourceRunId: '02-run',
      logicalStepId: 'apply',
      sourceExecutionOrdinal: 1,
      sourceCheckpointBoundary: 'after_execution',
      sourceCheckpointRef: 'artifact://checkpoint-apply',
      label: 'Try minimal API contract fix',
      state: 'promotable',
      branchKind: 'root',
      workspacePolicy: 'apply_previous_execution_diff_to_clean_baseline',
      runtimeContextPolicy: 'fresh_agent_run',
      gitRepository: 'MoonLadderStudios/MoonMind',
      gitBaseBranch: 'main',
      gitWorkBranch: 'mm/test-123/apply/branch-a',
      currentHeadStepExecutionId: 'step-head-a',
      currentHeadCheckpointRef: 'artifact://checkpoint-head-a',
      artifactRefs: { summary: 'artifact://branch-summary-a' },
      publishStatus: 'unpublished',
      createdAt: '2026-04-09T00:01:00Z',
      updatedAt: '2026-04-09T00:02:00Z',
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/checkpoint-branches/branch-a/promote') && init?.method === 'POST') {
        return Promise.resolve({ ok: true, json: async () => ({ ...branch, state: 'promoted' }) } as Response);
      }
      if (url.endsWith('/checkpoint-branches/branch-a/turns')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      if (url.endsWith('/checkpoint-branches')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [branch] }) } as Response);
      }
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => stepsWithCheckpoint } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Promote branch' }));

    await waitFor(() => {
      const postCall = fetchSpy.mock.calls.find(([url, init]) => (
        String(url).endsWith('/checkpoint-branches/branch-a/promote') &&
        (init as RequestInit | undefined)?.method === 'POST'
      ));
      expect(postCall).toBeTruthy();
      const body = JSON.parse(String((postCall?.[1] as RequestInit).body));
      expect(body.gateEvidence.verdict).toBe('passed');
      expect(body.sideEffectDisposition.status).toBe('none');
      expect(body.sideEffectDisposition.publishStatus).toBe('unpublished');
      expect(body.policyEvidence.freshHeadValidated).toBe(true);
      expect(body.idempotencyKey).toMatch(/^dashboard:promote:test-123:branch-a:/);
    });
  });

  it('MM-1105 does not show a global branch notice for action-specific blockers', async () => {
    window.history.pushState({}, 'Branch Notice Test', '/workflows/test-123/steps?source=temporal');
    const stepsWithCheckpoint = {
      ...latestStepsSnapshot,
      steps: latestStepsSnapshot.steps.map((step) => (
        step.logicalStepId === 'apply'
          ? { ...step, stateCheckpointRef: 'artifact://checkpoint-apply' }
          : step
      )),
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
      title: 'MM-1105 branch workflow',
      summary: 'Branch explorer detail',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
      relatedRuns: [],
    };
    const branch = {
      branchId: 'branch-a',
      workflowId: 'test-123',
      rootWorkflowId: 'test-123',
      sourceRunId: '02-run',
      logicalStepId: 'apply',
      sourceExecutionOrdinal: 1,
      sourceCheckpointBoundary: 'after_execution',
      sourceCheckpointRef: 'artifact://checkpoint-apply',
      label: 'Archived checkpoint branch',
      state: 'archived',
      branchKind: 'root',
      workspacePolicy: 'apply_previous_execution_diff_to_clean_baseline',
      runtimeContextPolicy: 'fresh_agent_run',
      gitRepository: 'MoonLadderStudios/MoonMind',
      gitBaseBranch: 'main',
      gitWorkBranch: 'mm/test-123/apply/branch-a',
      currentHeadStepExecutionId: 'step-head-a',
      currentHeadCheckpointRef: 'artifact://checkpoint-head-a',
      artifactRefs: { summary: 'artifact://branch-summary-a' },
      publishStatus: 'unpublished',
      createdAt: '2026-04-09T00:01:00Z',
      updatedAt: '2026-04-09T00:02:00Z',
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/checkpoint-branches/branch-a/turns')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      if (url.endsWith('/checkpoint-branches')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [branch] }) } as Response);
      }
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => stepsWithCheckpoint } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    expect(await screen.findByText('Archived checkpoint branch')).toBeTruthy();
    expect(screen.queryByText(/Branch action unavailable:/)).toBeNull();
    expect((screen.getByRole('button', { name: 'Continue branch' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Continue branch' }).getAttribute('title')).toBe(
      'Branch state archived cannot be changed.',
    );
    expect((screen.getByRole('button', { name: 'Publish branch' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('selects Chat by default and preserves query state in detail tab links', async () => {
    window.history.pushState({}, 'Default Chat Test', '/workflows/test-123?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Chat' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('link', { name: 'Chat' }).getAttribute('href')).toBe(
      '/workflows/test-123?source=temporal',
    );
    expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('href')).toBe(
      '/workflows/test-123/overview?source=temporal',
    );
    expect(screen.getByRole('link', { name: 'Execution' }).getAttribute('href')).toBe(
      '/workflows/test-123/execution?source=temporal',
    );
    expect(screen.getByRole('link', { name: 'Evidence' }).getAttribute('href')).toBe(
      '/workflows/test-123/evidence?source=temporal',
    );
  });

  it('deep-links to Chat and keeps non-chat tabs one click away without a file browser surface', async () => {
    window.history.pushState({}, 'Chat Deep Link Test', '/workflows/test-123/chat?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    expect((await screen.findByRole('link', { name: 'Chat' })).getAttribute('aria-current')).toBe('page');
    for (const label of ['Overview', 'Execution', 'Evidence', 'Debug']) {
      expect(screen.getByRole('link', { name: label })).toBeTruthy();
    }
    expect(screen.queryByRole('link', { name: /file browser/i })).toBeNull();
    expect(screen.queryByRole('heading', { name: /file browser/i })).toBeNull();
  });

  it('MM-801 renders Overview as a concise summary with stable segmented tabs', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Detail' })).toBeTruthy();
      expect(screen.queryByText(/Live updates enabled\. Polling every/i)).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
      expect(screen.getByText('Focused route summary')).toBeTruthy();
      expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBe('page');
      expect(screen.getByRole('link', { name: 'Execution' }).getAttribute('href')).toBe('/workflows/test-123/execution?source=temporal');
      expect(screen.getByRole('link', { name: 'Evidence' }).getAttribute('href')).toBe('/workflows/test-123/evidence?source=temporal');
      expect(screen.getByRole('link', { name: 'Execution' }).textContent).toContain('2');
      expect(screen.queryByRole('heading', { name: 'Workflow Steps' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Workflow Artifacts' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Execution History' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Run Comparison' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Timeline' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Report' })).toBeNull();
    });
  });

  it('keeps a zero step count badge instead of falling back to run count', async () => {
    window.history.pushState({}, 'Zero Step Badge Test', '/workflows/test-123/execution?source=temporal');
    mockWorkflowDetailSubrouteFetch({
      stepsSnapshot: { ...latestStepsSnapshot, steps: [] },
      relatedRuns: [],
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
      const executionLink = screen.getByRole('link', { name: 'Execution' });
      expect(executionLink.textContent).toContain('0');
      expect(executionLink.textContent).not.toContain('1');
    });
  });

  it('MM-1020 switches Workflow Detail tabs with pushState without remounting or preloading tab data', async () => {
    window.history.pushState({}, 'Client Tab Test', '/workflows/test-123/overview?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    expect(await screen.findByRole('heading', { name: 'Summary' })).toBeTruthy();
    expect(fetchSpy.mock.calls.filter(([input]) => String(input).includes('/executions/test-123/steps')).length).toBe(0);

    fireEvent.click(screen.getByRole('link', { name: 'Execution' }));

    expect(window.location.pathname).toBe('/workflows/test-123/execution');
    expect(await screen.findByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
    const stepsLink = await screen.findByRole('link', { name: 'Execution' });
    expect(stepsLink.getAttribute('aria-current')).toBe('page');
    expect(stepsLink.textContent).toContain('3');
    expect(fetchSpy.mock.calls.filter(([input]) => String(input).includes('/executions/test-123/steps')).length).toBeGreaterThan(0);
  });

  it('MM-1133 reuses cached detail tab evidence when returning to Steps inside the stale window', async () => {
    window.history.pushState({}, 'Detail Tab Cache Test', '/workflows/test-123/overview?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(
      <WorkflowDetailPage
        payload={{
          ...stepsPayload,
          initialData: {
            ...(stepsPayload.initialData as Record<string, unknown>),
            dashboardConfig: {
              ...((stepsPayload.initialData as { dashboardConfig: Record<string, unknown> }).dashboardConfig),
              pollIntervalsMs: { detail: 60000 },
            },
          },
        }}
      />,
    );

    expect(await screen.findByRole('heading', { name: 'Summary' })).toBeTruthy();
    const stepLedgerCalls = () => fetchSpy.mock.calls.filter(
      ([input]) => String(input) === '/api/executions/test-123/steps',
    );
    expect(stepLedgerCalls()).toHaveLength(0);

    fireEvent.click(screen.getByRole('link', { name: 'Execution' }));
    expect(await screen.findByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
    await waitFor(() => expect(stepLedgerCalls()).toHaveLength(1));

    fireEvent.click(screen.getByRole('link', { name: 'Evidence' }));
    expect(await screen.findByRole('heading', { name: 'Workflow Artifacts' })).toBeTruthy();
    expect(stepLedgerCalls()).toHaveLength(1);

    fireEvent.click(screen.getByRole('link', { name: 'Execution' }));
    expect(await screen.findByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
    expect(stepLedgerCalls()).toHaveLength(1);
  });

  it('keeps the standalone detail toolbar free of full-list navigation while preserving tab context (MM-998, MM-975)', async () => {
    window.history.pushState(
      {},
      'Detail Context Test',
      '/workflows/test-123/overview?source=temporal&stateIn=completed&repoContains=moon%2Frepo&limit=25&nextPageToken=cursor-2&sort=status&selectedWorkflowId=test-123&unsafe=1',
    );
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await screen.findByText('Focused route summary');
    expect(screen.queryByRole('link', { name: 'Expand to full list' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Execution' }).getAttribute('href')).toBe(
      '/workflows/test-123/execution?source=temporal&stateIn=completed&repoContains=moon%2Frepo&limit=25&nextPageToken=cursor-2&sort=status&selectedWorkflowId=test-123&unsafe=1',
    );
  });

  it('does not render standalone full-list navigation when no preserved list context exists (MM-998, MM-975)', async () => {
    window.history.pushState({}, 'Plain Detail Test', '/workflows/test-123/overview?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await screen.findByText('Focused route summary');
    expect(screen.queryByRole('link', { name: 'Expand to full list' })).toBeNull();
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

  it('MM-801 renders Execution as the focused step ledger and run history route', async () => {
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
      expect(screen.getByRole('link', { name: 'Execution' }).getAttribute('aria-current')).toBe('page');
      expect(screen.getByRole('heading', { name: 'Timeline' })).toBeTruthy();
      expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Workflow Artifacts' })).toBeNull();
      expect(screen.getByRole('heading', { name: 'Execution History' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Run Comparison' })).toBeTruthy();
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
          status: 'completed',
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
          logicalStepId: 'review',
          order: 2,
          title: 'Review plan',
          tool: { type: 'skill', name: 'plan.review', version: '1' },
          dependsOn: ['plan'],
          status: 'completed',
          waitingReason: null,
          attentionRequired: false,
          executionOrdinal: 1,
          startedAt: null,
          updatedAt: null,
          timing: {
            startedAt: null,
            endedAt: null,
            durationMs: null,
            elapsedMs: null,
            serverNow: '2026-04-09T00:00:04Z',
            precision: 'unavailable',
            preserved: true,
          },
          summary: 'Review preserved from the prior run',
          checks: [],
          refs: { childWorkflowId: null, childRunId: null, agentRunId: null },
          artifacts: {
            outputSummary: null,
            outputPrimary: 'art-review-output',
            runtimeStdout: null,
            runtimeStderr: null,
            runtimeMergedLogs: null,
            runtimeDiagnostics: null,
            providerSnapshot: null,
          },
          preservedFrom: {
            workflowId: 'test-123',
            runId: '01-run',
            logicalStepId: 'review',
            executionOrdinal: 1,
          },
          lastError: null,
        },
        {
          logicalStepId: 'apply',
          order: 3,
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
    expect(screen.getByText('Original duration: 1.0 s')).toBeTruthy();
    expect(screen.getByText('Original timing unavailable')).toBeTruthy();
    expect(screen.getByText('1.0 s')).toBeTruthy();
    expect(screen.getByLabelText('Step timing overview').textContent).toContain(
      'Longest step Plan work · 1.0 s',
    );

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

  it('MM-1122 renders remediation attempt cadence separately from gap progress', async () => {
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
      title: 'Remediation task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:00:04Z',
      actions: {},
    };
    const remediationSnapshot = {
      workflowId: 'test-123',
      runId: '02-run',
      runScope: 'latest',
      steps: [
        {
          logicalStepId: 'remediate-1',
          order: 1,
          title: 'Remediate verification gaps — attempt 1 of 6',
          tool: { type: 'skill', name: 'moonspec-implement', version: '1' },
          dependsOn: [],
          status: 'completed',
          annotations: {
            jiraOrchestrateRole: 'moonspec-remediation',
            moonSpecRemediationAttempt: 1,
            moonSpecRemediationMaxAttempts: 6,
          },
          executionOrdinal: 1,
          startedAt: '2026-04-09T00:00:01Z',
          updatedAt: '2026-04-09T00:00:02Z',
          summary: 'Addressed two known gaps',
          checks: [],
          refs: { childWorkflowId: null, childRunId: null, agentRunId: null },
          artifacts: {
            outputSummary: 'artifact://reports/remediation_attempt-1.json',
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
          logicalStepId: 'verify-1',
          order: 2,
          title: 'Verify remediation attempt 1 of 6',
          tool: { type: 'skill', name: 'moonspec-verify', version: '1' },
          dependsOn: ['remediate-1'],
          status: 'completed',
          annotations: {
            jiraOrchestrateRole: 'moonspec-verification-gate',
            moonSpecRemediationAttempt: 1,
            moonSpecRemediationMaxAttempts: 6,
          },
          executionOrdinal: 1,
          startedAt: '2026-04-09T00:00:03Z',
          updatedAt: '2026-04-09T00:00:04Z',
          summary: 'Verified the whole target state',
          checks: [],
          refs: { childWorkflowId: null, childRunId: null, agentRunId: null },
          artifacts: {
            outputSummary: 'artifact://reports/remediation_verification-1.json',
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
          logicalStepId: 'remediate-null-title',
          order: 3,
          title: null,
          tool: { type: 'skill', name: 'moonspec-implement', version: '1' },
          dependsOn: ['verify-1'],
          status: 'executing',
          annotations: {
            jiraOrchestrateRole: 'moonspec-remediation',
            moonSpecRemediationAttempt: 2,
            moonSpecRemediationMaxAttempts: 6,
          },
          executionOrdinal: 1,
          startedAt: '2026-04-09T00:00:05Z',
          updatedAt: '2026-04-09T00:00:06Z',
          summary: 'Remediating the next attempt',
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

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/step-executions')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            workflowId: 'test-123',
            runId: '02-run',
            runScope: 'latest',
            logicalStepId: 'remediate-1',
            stepExecutions: [],
          }),
        } as Response);
      }
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => remediationSnapshot } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getByText('Remediate verification gaps — attempt 1 of 6')).toBeTruthy();
      expect(screen.getByText('Verify remediation attempt 1 of 6')).toBeTruthy();
      expect(screen.getByText('Remediation · Attempt 1 of 6')).toBeTruthy();
      expect(screen.getByText('Full verification · Attempt 1 of 6')).toBeTruthy();
      expect(screen.getByText('Remediation · Attempt 2 of 6')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /Show details for Remediate verification gaps/ }));

    await waitFor(() => {
      expect(screen.getByText('Remediation cadence')).toBeTruthy();
      expect(screen.getByText('Recorded inside the remediation attempt artifact.')).toBeTruthy();
      expect(screen.getByText('Recorded inside the remediation attempt, not as sibling full-verifier steps.')).toBeTruthy();
    });
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
          timing: {
            startedAt: '2026-04-09T00:00:01Z',
            endedAt: '2026-04-09T00:04:03Z',
            durationMs: 242000,
            elapsedMs: 242000,
            serverNow: '2026-04-09T00:05:21Z',
            precision: 'exact',
          },
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
          status: 'completed',
          terminalDisposition: 'accepted',
          startedAt: '2026-04-09T00:00:03Z',
          updatedAt: '2026-04-09T00:00:04Z',
          timing: {
            startedAt: '2026-04-09T00:00:03Z',
            endedAt: '2026-04-09T00:01:21Z',
            durationMs: 78000,
            elapsedMs: 78000,
            serverNow: '2026-04-09T00:05:21Z',
            precision: 'exact',
          },
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
        return Promise.resolve({ ok: true, json: async () => latestStepsSnapshot } as Response);
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
    expect(history.parentElement?.textContent).toContain('2 step executions');
    expect(history.parentElement?.textContent).toContain('Total across executions: 5m 20s');

    // Newest execution renders first.
    const historyItems = Array.from(
      (history as HTMLElement).querySelectorAll('.step-execution-history-item'),
    );
    const ordinals = historyItems.map(
      (node) => node.querySelector('.step-execution-pill')?.textContent,
    );
    expect(ordinals).toEqual(['Execution 2', 'Execution 1']);
    expect(historyItems[0]?.textContent).toContain('1m 18s');
    expect(historyItems[1]?.textContent).toContain('4m 2s');

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

  it('MM-1034 shows step duration states and keeps the wall-clock timeline behind a secondary toggle', async () => {
    window.history.pushState({}, 'Step Timing Test', '/workflows/test-123/steps?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      title: 'Timing task',
      summary: 'Execution summary',
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      temporalStatus: 'running',
      createdAt: '2026-04-09T00:00:00Z',
      updatedAt: '2026-04-09T00:05:00Z',
      actions: {},
    };
    const timingSnapshot = {
      workflowId: 'test-123',
      runId: '02-run',
      runScope: 'latest',
      steps: [
        {
          logicalStepId: 'gather',
          order: 1,
          title: 'Gather context',
          tool: { type: 'skill', name: 'context.gather', version: '1' },
          dependsOn: [],
          status: 'completed',
          waitingReason: null,
          attentionRequired: false,
          executionOrdinal: 1,
          startedAt: '2026-04-09T00:00:00Z',
          updatedAt: '2026-04-09T00:01:42Z',
          timing: {
            startedAt: '2026-04-09T00:00:00Z',
            endedAt: '2026-04-09T00:01:42Z',
            durationMs: 102000,
            elapsedMs: 102000,
            serverNow: '2026-04-09T00:05:00Z',
            precision: 'exact',
            preserved: false,
          },
          summary: 'Gathered context',
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
          workload: { durationSeconds: 999 },
          lastError: null,
        },
        {
          logicalStepId: 'test',
          order: 2,
          title: 'Run tests',
          tool: { type: 'agent_runtime', name: 'codex_cli', version: '1' },
          dependsOn: ['gather'],
          status: 'executing',
          waitingReason: null,
          attentionRequired: false,
          executionOrdinal: 2,
          startedAt: '2026-04-09T00:02:00Z',
          updatedAt: '2026-04-09T00:05:11Z',
          timing: {
            startedAt: '2026-04-09T00:02:00Z',
            endedAt: null,
            durationMs: null,
            elapsedMs: 191000,
            serverNow: '2026-04-09T00:05:11Z',
            precision: 'live',
            preserved: false,
          },
          summary: 'Running tests',
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
          workload: null,
          lastError: null,
        },
        {
          logicalStepId: 'publish',
          order: 3,
          title: 'Publish results',
          tool: { type: 'skill', name: 'publish.results', version: '1' },
          dependsOn: ['test'],
          status: 'pending',
          waitingReason: null,
          attentionRequired: false,
          executionOrdinal: 0,
          startedAt: null,
          updatedAt: '2026-04-09T00:05:11Z',
          timing: {
            startedAt: null,
            endedAt: null,
            durationMs: null,
            elapsedMs: null,
            serverNow: '2026-04-09T00:05:11Z',
            precision: 'unavailable',
            preserved: false,
          },
          summary: 'Waiting for tests',
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
          workload: null,
          lastError: null,
        },
        {
          logicalStepId: 'ready',
          order: 4,
          title: 'Ready follow-up',
          tool: { type: 'skill', name: 'follow.up', version: '1' },
          dependsOn: [],
          status: 'ready',
          waitingReason: null,
          attentionRequired: false,
          executionOrdinal: 0,
          startedAt: null,
          updatedAt: '2026-04-09T00:05:11Z',
          summary: 'Ready',
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
          workload: null,
          lastError: null,
        },
        {
          logicalStepId: 'unknown',
          order: 5,
          title: 'Unknown timing',
          tool: { type: 'skill', name: 'unknown.timing', version: '1' },
          dependsOn: [],
          status: 'completed',
          waitingReason: null,
          attentionRequired: false,
          executionOrdinal: 1,
          startedAt: null,
          updatedAt: '2026-04-09T00:05:11Z',
          timing: {
            startedAt: null,
            endedAt: null,
            durationMs: null,
            elapsedMs: null,
            serverNow: '2026-04-09T00:05:11Z',
            precision: 'unavailable',
            preserved: false,
          },
          summary: 'No timing',
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
          workload: null,
          lastError: null,
        },
      ],
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => timingSnapshot } as Response);
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

    expect(await screen.findByText('1m 42s')).toBeTruthy();
    expect(screen.getByText('3m 11s so far')).toBeTruthy();
    expect(screen.getByText('Not started')).toBeTruthy();
    expect(screen.getAllByText('Ready').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Timing unavailable')).toBeTruthy();
    expect(screen.queryByLabelText('Step duration 1m 42s')).toBeNull();
    expect(screen.queryByLabelText('Step duration 3m 11s')).toBeNull();
    expect(screen.getByLabelText('Step timing overview').textContent).toContain(
      'Current step Run tests · 3m 11s so far',
    );
    expect(screen.getByLabelText('Step timing overview').textContent).toContain(
      'Longest step Run tests · 3m 11s',
    );
    expect(screen.getByLabelText('Step timing overview').textContent).toContain(
      'Completed steps 2 of 5',
    );
    expect(screen.queryByRole('region', { name: 'Step duration timeline' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Show step duration timeline' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Hide step duration timeline' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Show details for Gather context' }));
    const timingHeading = screen.getByRole('heading', { name: 'Timing' });
    const logsHeading = screen.getByRole('heading', { name: 'Logs & Diagnostics' });
    expect(timingHeading.compareDocumentPosition(logsHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const timingSection = timingHeading.closest('section') as HTMLElement;
    expect(within(timingSection).getByText('Started:')).toBeTruthy();
    expect(within(timingSection).getByText('Ended:')).toBeTruthy();
    expect(within(timingSection).getByText('Elapsed:')).toBeTruthy();
    expect(within(timingSection).getByText('Last update:')).toBeTruthy();
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
      expect(screen.getByRole('link', { name: 'Evidence' }).getAttribute('aria-current')).toBe('page');
      expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Step DAG' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Workflow Steps' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Timeline' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Execution History' })).toBeNull();
      expect(screen.queryByRole('heading', { name: 'Run Comparison' })).toBeNull();
    });
  });

  it('MM-801 maps Runs to the combined Execution route', async () => {
    window.history.pushState({}, 'Runs Test', '/workflows/test-123/runs?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: 'Execution History' }).length).toBeGreaterThan(0);
      expect(screen.getByRole('heading', { name: 'Run Comparison' })).toBeTruthy();
      expect(screen.getAllByText('test-456').length).toBeGreaterThan(0);
      expect(screen.getByRole('link', { name: 'Execution' }).getAttribute('aria-current')).toBe('page');
      expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
      expect(screen.getByRole('heading', { name: 'Workflow Steps' })).toBeTruthy();
      expect(screen.queryByRole('heading', { name: 'Workflow Artifacts' })).toBeNull();
      expect(screen.getByRole('heading', { name: 'Timeline' })).toBeTruthy();
      expect(screen.queryByRole('heading', { name: 'Report' })).toBeNull();
    });
  });

  it('MM-957 keeps raw Temporal facts out of the default overview while preserving summary and evidence sections', async () => {
    window.history.pushState({}, 'Overview Debug IA Test', '/workflows/test-123/overview?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    await waitFor(() => {
      // Summary renders on the default overview without the old preview cards.
      expect(screen.getByRole('heading', { name: 'Summary' })).toBeTruthy();
      expect(screen.getByText('Focused route summary')).toBeTruthy();
      expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
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
      expect(screen.getByRole('link', { name: 'Debug' }).getAttribute('aria-current')).toBe('page');
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

    // Debug is a first-class tab: reachable in one click and marked current on its route.
    expect(screen.getByRole('link', { name: 'Debug' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('link', { name: 'Debug' }).getAttribute('href')).toBe(
      '/workflows/test-123/debug?source=temporal',
    );
    // Overview is reachable from the Debug subroute (deep links round-trip).
    expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('href')).toBe('/workflows/test-123/overview?source=temporal');

    // Raw Temporal facts are scoped to Debug only — not the Overview preview cards.
    expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
  });

  it('MM-1020 keeps the Debug tab stable while the kebab menu toggles debug details', async () => {
    window.history.pushState({}, 'Debug Pref Test', '/workflows/test-123/overview?source=temporal');
    mockWorkflowDetailSubrouteFetch();

    const view = renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);

    // Debug is a first-class tab by default.
    expect(await screen.findByRole('link', { name: 'Debug' })).toBeTruthy();

    let menu = await openWorkflowActionsMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'View: Hide debug details' }));

    // The Debug tab remains visible and the preference is persisted.
    expect(screen.getByRole('link', { name: 'Debug' })).toBeTruthy();
    expect(window.localStorage.getItem('moonmind.dashboard.preferences')).toContain(
      '"debugFieldsVisible":false',
    );

    // Simulate a reload: a fresh mount keeps the Debug tab present.
    view.unmount();
    renderWithClient(<WorkflowDetailPage payload={stepsPayload} />);
    await screen.findByRole('link', { name: 'Overview' });
    expect(screen.getByRole('link', { name: 'Debug' })).toBeTruthy();
    menu = await openWorkflowActionsMenu();
    expect(within(menu).getByRole('menuitem', { name: 'View: Show debug details' })).toBeTruthy();
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

  it('renders a Steps section above Timeline without preloading inactive artifact data', async () => {
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
      expect(detailIndex).toBeGreaterThanOrEqual(0);
      expect(stepsIndex).toBeGreaterThan(detailIndex);
      expect(urls.findIndex((url) => url.includes('/artifacts'))).toBe(-1);
    });
  });

  it('renders the required workflow execution identity and section labels', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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
      expect(screen.queryByRole('heading', { name: 'Workflow Preview' })).toBeNull();
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
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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
      expect(screen.getByRole('heading', { name: 'Proposal Outcomes' })).toBeTruthy();
      const metricText = (label: string) =>
        screen.getAllByText(label)
          .map((element) => element.closest('.metric-strip-item')?.textContent || '')
          .find(Boolean) || '';
      expect(metricText('Delivered')).toContain('1');
      expect(metricText('Updated')).toContain('1');
      expect(metricText('Failed')).toContain('2');
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
      expect(urls.some((url) => url.includes('/artifacts'))).toBe(false);
    });

    const callsAfterInitialLoad = detailSurfaceCalls();
    await new Promise((resolve) => setTimeout(resolve, 25));

    expect(detailSurfaceCalls()).toEqual(callsAfterInitialLoad);
  });

  it('displays original slash instructions and missing runtime command metadata state', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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


  it('renders planning detail pills with setup coloring and keeps dependency pills inactive when appropriate', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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
    const toolbarStatus = document.querySelector<HTMLElement>('.toolbar-identity-row span.status');
    expect(toolbarStatus?.dataset.effect).toBe('shimmer-sweep');
    expect(toolbarStatus?.dataset.state).toBe('planning');
    expect(toolbarStatus?.className).toContain('status-planning');
    expect(toolbarStatus?.className).toContain('is-planning');
    expect(toolbarStatus?.className).not.toContain('status-running');
    expect(toolbarStatus?.dataset.shimmerLabel).toBe('Planning');
    expect(toolbarStatus?.getAttribute('aria-label')).toBe('Planning');
    expect(toolbarStatus?.querySelector('.status-letter-wave')?.getAttribute('data-label')).toBe('Planning');
    expect(toolbarStatus?.textContent).toBe('Planning');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-489');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-490');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-491');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-1035');

    const waitingPill = await screen.findByText('Awaiting dependencies');
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
    window.history.pushState({}, 'Artifacts Test', '/workflows/test-123/artifacts?source=temporal');
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
      expect(screen.getByRole('heading', { name: 'Workflow Artifacts' })).toBeTruthy();
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
    const completedIcon = await screen.findByLabelText('Status: completed');
    expect(completedIcon.classList.contains('step-tl-icon')).toBe(true);
    expect(completedIcon.querySelector('svg.lucide-check')).toBeTruthy();
    expect(screen.getByText('completed')).toBeTruthy();
    const executingIcon = await screen.findByLabelText('Status: executing');
    expect(executingIcon.classList.contains('step-tl-icon')).toBe(true);
    expect(executingIcon.querySelector('svg.lucide-play')).toBeTruthy();
    expect(screen.getAllByText('executing').length).toBeGreaterThan(0);
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
                        status: 'pending',
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
      const activeCheckBadge = document.querySelector<HTMLElement>(
        '.step-check-badge[data-effect="shimmer-sweep"]',
      );
      expect(activeCheckBadge).toBeTruthy();
      expect(activeCheckBadge?.textContent).toBe('approval policy: pending');
      expect(activeCheckBadge?.dataset.effect).toBe('shimmer-sweep');
      expect(activeCheckBadge?.dataset.state).toBe('executing');
      expect(activeCheckBadge?.className).toContain('is-executing');
      expect(activeCheckBadge?.className).toContain('check-pending');
      expect(activeCheckBadge?.getAttribute('aria-label')).toBe('approval policy: pending');
      expect(activeCheckBadge?.querySelector('.status-letter-wave')?.getAttribute('data-label')).toBe(
        'approval policy: pending',
      );
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
    expect(screen.getByText('Workflow detail summary loading placeholder').closest('[role="status"]')).toBeTruthy();
    expect(screen.getByTestId('loading-placeholder-detail')).toBeTruthy();
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

    const menu = await openWorkflowActionsMenu('Edit');
    expect(within(menu).getByRole('menuitem', { name: 'Edit' }).getAttribute('href')).toBe(
      '/workflows/new?editExecutionId=test-123',
    );
    const editLink = within(menu).getByRole('menuitem', { name: 'Edit' });
    editLink.addEventListener('click', (event) => event.preventDefault());
    fireEvent.focus(editLink);
    fireEvent.keyDown(editLink, { key: 'Enter' });
    fireEvent.click(editLink);
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
    const viewport = await screen.findByLabelText('Dashboard notifications');
    const toast = within(viewport).getByRole('status');
    expect(within(toast).getByText('Rerun requested')).toBeTruthy();
    expect(within(toast).getByText('Editable task has been queued.')).toBeTruthy();
    expect(within(toast).getByRole('link', { name: 'View workflow' }).getAttribute('href')).toBe(
      '/workflows/test-123/steps?source=temporal',
    );
    expect(
      screen.queryByText('Rerun was requested and the latest execution view is ready.'),
    ).toBeNull();
    expect(telemetryEvents).toEqual(
      expect.arrayContaining([
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
          status: 'completed',
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
    const menu = await openWorkflowActionsMenu('Rename');
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
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

    const menu = await openWorkflowActionsMenu('Cancel');
    expect(within(menu).getByRole('menuitem', { name: 'Cancel' })).toBeTruthy();
    expect(within(menu).queryByRole('menuitem', { name: 'Remediate' })).toBeNull();
  });

  it('routes menu selections through direct lifecycle handlers', async () => {
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

    let menu = await openWorkflowActionsMenu('Rename');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Rename' }));
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

    menu = await openWorkflowActionsMenu('Pause');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Pause' }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/signal',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ signalName: 'Pause', payload: {} }),
        }),
      );
    });

    menu = await openWorkflowActionsMenu('Cancel');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Cancel' }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/cancel',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ action: 'cancel', graceful: true }),
        }),
      );
    });

    menu = await openWorkflowActionsMenu('Force cancel');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Force cancel' }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/cancel',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ action: 'cancel', graceful: false }),
        }),
      );
    });
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

    await screen.findByText('Keyboard task');
    const trigger = await screen.findByRole('button', { name: 'Workflow actions' });
    fireEvent.keyDown(trigger, { key: 'Enter' });
    expect(await screen.findByRole('menuitem', { name: 'Pause' })).toBeTruthy();
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

  it('shows only the view action when no workflow operations are available', async () => {
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

    const menu = await openWorkflowActionsMenu('View: Hide debug details');
    expect(within(menu).getByRole('menuitem', { name: 'View: Hide debug details' })).toBeTruthy();
    expect(within(menu).queryByRole('menuitem', { name: 'Rename' })).toBeNull();
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
    expect(
      screen
        .getAllByRole('status')
        .some((status) => status.textContent?.includes('Changes were saved to this execution.')),
    ).toBe(true);
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

    let menu = await openWorkflowActionsMenu('Rerun');
    expect(within(menu).getByRole('menuitem', { name: 'Rerun' })).toBeTruthy();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Rename' }));
    fireEvent.change(screen.getByLabelText('Workflow title'), {
      target: { value: 'Renamed task' },
    });
    confirmWorkflowDialog('Rename workflow');

    await waitFor(() => {
      expect(fetchSpy.mock.calls.some(([input]) => String(input).includes('/update'))).toBe(true);
    });

    menu = await openWorkflowActionsMenu('Rerun');
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

    const menu = await openWorkflowActionsMenu('Edit');
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

    const menu = await openWorkflowActionsMenu('Edit');
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

    const menu = await openWorkflowActionsMenu('Edit');
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

    const menu = await openWorkflowActionsMenu('Resume from failed step');
    const resumeButton = within(menu).getByRole('menuitem', { name: 'Resume from failed step' });
    expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull();
    fireEvent.click(resumeButton);
    expect(screen.queryByRole('dialog', { name: 'Resume from failed step' })).toBeNull();

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
          status: 'completed',
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
    const menu = await openWorkflowActionsMenu('Recover from selected step');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Recover from selected step' }));
    expect(screen.queryByRole('dialog', { name: 'Recover from selected step' })).toBeNull();

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
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      targetRuntime: 'claude_code',
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
      profileId: 'profile:claude-default',
      providerId: 'anthropic',
      providerLabel: 'Anthropic',
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
      expect(screen.getByText('Claude Code')).toBeTruthy();
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
      expect(screen.getByText('Anthropic')).toBeTruthy();
      expect(screen.getByText('profile:claude-default')).toBeTruthy();
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
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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
                checkpointBranches: [
                  {
                    workflowId: 'test-123',
                    branchId: 'cbr-remediation-inbound',
                    branchTurnId: 'cbt-remediation-inbound',
                    checkpointRef: 'artifact://checkpoints/inbound',
                    contextArtifactRef: 'art_context',
                    rootCheckpointRef: 'artifact://workspace/C0',
                    rootWorkspaceDigest: 'sha256:root',
                    headCheckpointRef: 'artifact://workspace/C2',
                    headWorkspaceDigest: 'sha256:candidate-two',
                    headAttemptOrdinal: 2,
                    headVersion: 3,
                    headStatus: 'verified_incomplete',
                    latestVerificationVerdict: 'ADDITIONAL_WORK_NEEDED',
                    remainingWorkRef: 'artifact://verification/V2#remainingWork',
                    nextActionBaseline: {
                      checkpointRef: 'artifact://workspace/C2',
                      workspaceDigest: 'sha256:candidate-two',
                      headVersion: 3,
                    },
                  },
                ],
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
                checkpointBranches: [
                  {
                    workflowId: 'mm:target-1',
                    branchId: 'cbr-remediation-outbound',
                    branchTurnId: 'cbt-remediation-outbound',
                    checkpointRef: 'artifact://checkpoints/outbound',
                  },
                ],
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

    const menu = await openWorkflowActionsMenu('Remediate');
    expect(within(menu).getByRole('menuitem', { name: 'Remediate' })).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Remediation' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Remediation Workflows' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Remediation Target' })).toBeTruthy();
    expect(screen.getByText('mm:remediation-1')).toBeTruthy();
    expect(screen.getAllByText('mm:target-1').length).toBeGreaterThan(0);
    expect(screen.getByText('cbr-remediation-inbound')).toBeTruthy();
    expect(screen.getByText('cbr-remediation-outbound')).toBeTruthy();
    expect(screen.getByText('verified incomplete')).toBeTruthy();
    expect(screen.getByText('2 / 3')).toBeTruthy();
    expect(screen.getByText('artifact://workspace/C2 @ v3')).toBeTruthy();
    expect(screen.getByText('artifact://verification/V2#remainingWork')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Remediation Evidence' })).toBeTruthy();
    expect(screen.getByText('Context')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Evidence' }).getAttribute('href')).toBe(
      '/api/artifacts/art_context/download',
    );

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

    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Remediate' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/new');
      expect(window.location.search).toContain('intent=remediate');
    });
    const draftKey = Array.from({ length: window.sessionStorage.length })
      .map((_, index) => window.sessionStorage.key(index) || '')
      .find((key) => key.startsWith('moonmind.remediation-create-draft.'));
    expect(draftKey).toBeTruthy();
    const remediationDraft = JSON.parse(String(window.sessionStorage.getItem(String(draftKey))));
    expect(remediationDraft).not.toHaveProperty('targetRuntime');
    expect(remediationDraft).not.toHaveProperty('profileId');
    expect(remediationDraft).toMatchObject({
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
    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) => String(url) === '/api/executions/test-123/remediation' && init?.method === 'POST',
      ),
    ).toBe(false);
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
    const menu = await openWorkflowActionsMenu('Remediate');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Remediate' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/new');
      expect(window.location.search).toContain('intent=remediate');
    });
    const draftKey = Array.from({ length: window.sessionStorage.length })
      .map((_, index) => window.sessionStorage.key(index) || '')
      .find((key) => key.startsWith('moonmind.remediation-create-draft.'));
    expect(draftKey).toBeTruthy();
    expect(JSON.parse(String(window.sessionStorage.getItem(String(draftKey))))).toMatchObject({
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
    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) =>
          String(url) === '/api/executions/test-remediation-create-choices/remediation' &&
          init?.method === 'POST',
      ),
    ).toBe(false);
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
    const dashboardCss = await readDashboardCss();

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
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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

  it('renders auto publish mode and evidence with Auto labels', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-auto-publish/overview?source=temporal');
    const mockExecution = {
      taskId: 'test-auto-publish',
      workflowId: 'test-auto-publish',
      namespace: 'default',
      temporalRunId: '01-run',
      runId: '01-run',
      source: 'temporal',
      workflowType: 'MoonMind.UserWorkflow',
      entry: 'user_workflow',
      title: 'Auto publish task',
      summary: 'Auto publish verified',
      status: 'completed',
      state: 'succeeded',
      rawState: 'succeeded',
      temporalStatus: 'completed',
      closeStatus: 'COMPLETED',
      summaryArtifactRef: 'art-summary-auto',
      publishMode: 'auto',
      createdAt: '2026-03-28T00:00:00Z',
      startedAt: '2026-03-28T00:00:01Z',
      updatedAt: '2026-03-28T00:00:02Z',
      closedAt: '2026-03-28T00:00:03Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/artifacts/art-summary-auto/download')) {
        return Promise.resolve({
          ok: true,
          text: async () =>
            JSON.stringify({
              finishOutcome: {
                code: 'PUBLISHED_BRANCH',
                stage: 'publish',
              },
              publish: {
                mode: 'auto',
                owner: 'agent',
                status: 'verified',
                reason: 'Auto publish verified.',
              },
              publishContext: {
                branch: 'feature/auto-publish',
                baseRef: 'origin/main',
                commitCount: 1,
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
      expect(screen.getByText('Auto publish task')).toBeTruthy();
      expect(screen.getByText('Run Summary')).toBeTruthy();
      expect(screen.getAllByText('Auto').length).toBeGreaterThanOrEqual(2);
      expect(screen.getByText('Auto publish verified.')).toBeTruthy();
      expect(screen.getByText('feature/auto-publish')).toBeTruthy();
    });
    expect(screen.queryByText('auto')).toBeNull();
  });

  it('does not render a PR link for unsafe execution or run-summary URLs', async () => {
    window.history.pushState({}, 'Overview Test', '/workflows/test-123/overview?source=temporal');
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
    window.history.pushState({}, 'Overview Test', '/workflows/test-merge-visibility/overview?source=temporal');
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
    window.history.pushState({}, 'Overview Test', '/workflows/test-live-merge-visibility/overview?source=temporal');
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
    window.history.pushState({}, 'Overview Test', '/workflows/test-null-merge-artifact-refs/overview?source=temporal');
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
    window.history.pushState({}, 'Overview Test', '/workflows/mm%3Adependent-1/overview?source=temporal');
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

    window.history.pushState({}, 'Test', '/workflows/mm%3Adependent-1/overview?source=temporal');
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    const menu = await openWorkflowActionsMenu('Bypass Dependencies');
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

  it('renders bridge session events before managed-runtime missing copy', async () => {
    window.history.pushState({}, 'Bridge Chat Test', '/workflows/test-123/chat?source=temporal');
    const mockExecution = {
      taskId: 'test-123',
      workflowId: 'test-123',
      namespace: 'default',
      temporalRunId: 'non-uuid-run',
      runId: 'non-uuid-run',
      source: 'temporal',
      title: 'Bridge task',
      summary: 'Completed through bridge',
      status: 'completed',
      state: 'completed',
      rawState: 'completed',
      closedAt: '2026-07-09T00:00:30Z',
      createdAt: '2026-07-09T00:00:00Z',
      updatedAt: '2026-07-09T00:00:30Z',
      actions: {},
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/omnigent/bridge-sessions/resolve')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            bridgeSessionId: 'brs-1',
            workflowId: 'test-123',
            status: 'completed',
          }),
        } as Response);
      }
      if (url.includes('/omnigent/bridge-sessions/brs-1/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            schemaVersion: 'moonmind.bridge-session-events-page.v1',
            bridgeSessionId: 'brs-1',
            items: [
              {
                sequence: 1,
                timestamp: '2026-07-09T00:00:10Z',
                stream: 'stdout',
                text: 'Bridge assistant output',
                kind: 'assistant_message',
                sessionId: 'brs-1',
                metadata: { responseId: 'resp-1', source: 'omnigent_bridge' },
              },
            ],
            after: 0,
            nextCursor: '1',
            hasMore: false,
            terminal: true,
            latestSequence: 1,
            retentionGap: null,
            terminalEnvelope: {
              schemaVersion: 'moonmind.bridge-session-terminal.v1',
              status: 'completed',
            },
          }),
        } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => {
      expect(screen.getAllByText('Bridge assistant output').length).toBeGreaterThan(0);
    });
    expect(screen.queryByText(/managed runtime observability record was created/i)).toBeNull();
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url).includes('/agent-runs/')),
    ).toBe(false);
  });

  it.each([
    ['direct Codex compatibility', 'codex_direct_compat'],
    ['Omnigent', 'omnigent_bridge'],
  ])('projects an active %s journey through shared history, SSE, chat, and resources', async (_label, source) => {
    window.history.pushState({}, 'Bridge parity journey', '/workflows/test-123/chat?source=temporal');
    const priorEventSource = window.EventSource;
    window.EventSource = MockEventSource as unknown as typeof EventSource;
    const bridgeSessionId = `brs-parity-${source}`;
    const execution = {
      taskId: 'test-123', workflowId: 'test-123', namespace: 'default', source: 'temporal',
      temporalRunId: 'parity-run', runId: 'parity-run', title: 'Active parity journey',
      summary: 'Streaming', status: 'running', state: 'executing', rawState: 'running',
      createdAt: '2026-07-09T00:00:00Z', updatedAt: '2026-07-09T00:00:10Z', actions: {},
    };
    const event = (sequence: number, kind: string, text: string, metadata: Record<string, unknown> = {}) => ({
      sequence, timestamp: `2026-07-09T00:00:${String(sequence).padStart(2, '0')}Z`,
      stream: 'stdout' as const, kind, text, sessionId: bridgeSessionId,
      metadata: { source, directSessionId: source === 'codex_direct_compat' ? 'sess-direct' : undefined, ...metadata },
    });

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/omnigent/bridge-sessions/resolve')) return Promise.resolve({ ok: true, json: async () => ({
        bridgeSessionId, workflowId: 'test-123', status: 'running', providerSessionRef: `${source}-provider`,
        capabilities: {},
      }) } as Response);
      if (url.includes(`/${bridgeSessionId}/events`)) return Promise.resolve({ ok: true, json: async () => ({
        schemaVersion: 'moonmind.bridge-session-events-page.v1', bridgeSessionId,
        items: [
          event(1, 'assistant_message', 'Shared assistant progress'),
          event(2, 'tool_started', 'Running tests', { toolName: 'shell' }),
          event(3, 'approval_requested', 'Approval requested', { elicitationId: 'approval-1' }),
        ],
        after: 0, nextCursor: '3', hasMore: false, terminal: false, latestSequence: 3,
      }) } as Response);
      if (url.includes(`/${bridgeSessionId}/resources`)) return Promise.resolve({ ok: true, json: async () => ({
        schemaVersion: 'moonmind.omnigent.resource_projection.v1', bridgeSessionId, completeness: 'harvesting',
        groups: [{ groupKey: 'artifacts', title: 'Artifacts', resources: [{
          label: 'test-results.txt', artifactRef: 'artifact:test-results', status: 'available',
          previewAvailable: true, downloadAvailable: true, sourceEventSequence: 2,
        }] }],
      }) } as Response);
      if (url.includes('/artifacts')) return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      return Promise.resolve({ ok: true, json: async () => execution } as Response);
    });

    try {
      renderWithClient(<WorkflowDetailPage payload={mockPayload} />);
      expect((await screen.findAllByText('Shared assistant progress')).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/Running tests/).length).toBeGreaterThan(0);
      expect(screen.getByText('test-results.txt')).toBeTruthy();
      expect(screen.getByRole('link', { name: 'Open test-results.txt' })).toBeTruthy();
      await waitForEventSourceInstance();
      const stream = MockEventSource.instances.at(-1)!;
      expect(stream.url).toContain(`/${bridgeSessionId}/stream`);
      act(() => stream.triggerMessage(event(4, 'assistant_message', 'Shared live delta')));
      expect((await screen.findAllByText('Shared live delta')).length).toBeGreaterThan(0);
      expect(screen.getByTestId('chat-session-viewer')).toBeTruthy();
    } finally {
      window.EventSource = priorEventSource;
    }
  });

  it('renders an understandable failed-before-stream lifecycle with zero provider events', async () => {
    window.history.pushState({}, 'Failed Launch Chat', '/workflows/test-123/chat?source=temporal');
    const mockExecution = {
      taskId: 'test-123', workflowId: 'test-123', namespace: 'default',
      temporalRunId: 'failed-launch-run', runId: 'failed-launch-run', source: 'temporal',
      title: 'Failed launch', summary: 'Launch stopped before provider streaming.',
      status: 'failed', state: 'failed', rawState: 'failed',
      closedAt: '2026-07-09T00:00:30Z', createdAt: '2026-07-09T00:00:00Z',
      updatedAt: '2026-07-09T00:00:30Z', actions: {},
    };
    const lifecycleItem = (
      sequence: number,
      stage: string,
      status: string,
      metadata: Record<string, unknown> = {},
    ) => ({
      sequence,
      timestamp: `2026-07-09T00:00:${String(sequence).padStart(2, '0')}Z`,
      stream: 'stdout',
      text: '',
      kind: `lifecycle_${stage}`,
      sessionId: 'brs-failed-launch',
      metadata: { status, ...metadata },
    });

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/omnigent/bridge-sessions/resolve')) {
        return Promise.resolve({ ok: true, json: async () => ({
          bridgeSessionId: 'brs-failed-launch', workflowId: 'test-123', status: 'failed',
        }) } as Response);
      }
      if (url.includes('/omnigent/bridge-sessions/brs-failed-launch/events')) {
        return Promise.resolve({ ok: true, json: async () => ({
          schemaVersion: 'moonmind.bridge-session-events-page.v1',
          bridgeSessionId: 'brs-failed-launch',
          items: [
            lifecycleItem(1, 'profile_readiness', 'ready'),
            lifecycleItem(2, 'credential_preflight', 'failed', {
              code: 'oauth_generation_mismatch',
              summary: 'Credential generation did not match the mounted volume.',
              failureClass: 'configuration_error',
              remediationAction: 'validate_codex_oauth',
              diagnosticsRef: 'artifact://launch/diagnostics',
              metadata: {
                providerProfileId: 'codex', hostLeaseRef: 'host-lease-1',
                workflowId: 'test-123', stepExecutionId: 'step-1',
              },
            }),
            lifecycleItem(3, 'host_cleanup', 'completed', {
              metadata: { cleanupCompleted: true, hostLeaseReleased: true },
            }),
            lifecycleItem(4, 'terminal', 'failed', {
              metadata: { cleanupCompleted: true, leaseReleased: true, workflowId: 'test-123' },
            }),
          ],
          after: 0, nextCursor: '4', hasMore: false, terminal: true,
          latestSequence: 4, retentionGap: null,
          terminalEnvelope: { schemaVersion: 'moonmind.bridge-session-terminal.v1', status: 'failed' },
        }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => expect(screen.getAllByText(/credential preflight: failed/i).length).toBeGreaterThan(0));
    expect(screen.getAllByText(/profile readiness: ready/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Reason: oauth_generation_mismatch/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Profile: codex/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Host lease: host-lease-1/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Cleanup: completed/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Profile lease: released/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Recommended action: validate codex oauth/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/terminal: failed/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Open diagnostics').length).toBeGreaterThan(0);
    expect(screen.queryByText(/managed runtime observability record was created/i)).toBeNull();
    expect(screen.queryByText('Bridge assistant output')).toBeNull();
  });

  it('shows an authorization error instead of resource preview or download actions', async () => {
    window.history.pushState({}, 'Bridge Authorization Test', '/workflows/test-123/chat?source=temporal');
    const execution = {
      taskId: 'test-123', workflowId: 'test-123', namespace: 'default',
      temporalRunId: 'auth-run', runId: 'auth-run', source: 'temporal',
      title: 'Protected bridge task', summary: 'Protected evidence',
      status: 'completed', state: 'completed', rawState: 'completed',
      closedAt: '2026-07-09T00:00:30Z', createdAt: '2026-07-09T00:00:00Z',
      updatedAt: '2026-07-09T00:00:30Z', actions: {},
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/omnigent/bridge-sessions/resolve')) {
        return Promise.resolve({ ok: true, json: async () => ({ bridgeSessionId: 'brs-auth', workflowId: 'test-123', status: 'completed' }) } as Response);
      }
      if (url.includes('/omnigent/bridge-sessions/brs-auth/events')) {
        return Promise.resolve({ ok: true, json: async () => ({
          schemaVersion: 'moonmind.bridge-session-events-page.v1', bridgeSessionId: 'brs-auth',
          items: [], after: 0, nextCursor: null, hasMore: false, terminal: true,
          latestSequence: 0, retentionGap: null, terminalEnvelope: null,
        }) } as Response);
      }
      if (url.includes('/omnigent/bridge-sessions/brs-auth/resources')) {
        return Promise.resolve({ ok: false, status: 403 } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => execution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    expect(await screen.findByText('You do not have permission to view observability for this run.')).toBeTruthy();
    expect(screen.queryByRole('link', { name: /^Open .*\.py$/ })).toBeNull();
    expect(screen.queryByRole('link', { name: /^Download .*\.py$/ })).toBeNull();
  });

  it('renders bridge terminal failure evidence even without provider deltas', async () => {
    window.history.pushState({}, 'Bridge Failure Test', '/workflows/test-123/chat?source=temporal');
    const mockExecution = {
      taskId: 'test-123', workflowId: 'test-123', source: 'temporal', namespace: 'default',
      title: 'Bridge failure', summary: 'Failed before streaming',
      createdAt: '2026-07-09T00:00:00Z', updatedAt: '2026-07-09T00:00:30Z',
      status: 'failed', state: 'failed', rawState: 'failed', actions: {},
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/omnigent/bridge-sessions/resolve')) {
        return Promise.resolve({ ok: true, json: async () => ({ bridgeSessionId: 'brs-failed', status: 'failed' }) } as Response);
      }
      if (url.includes('/omnigent/bridge-sessions/brs-failed/events')) {
        return Promise.resolve({ ok: true, json: async () => ({
          schemaVersion: 'moonmind.bridge-session-events-page.v1', bridgeSessionId: 'brs-failed',
          items: [], after: 0, nextCursor: null, hasMore: false, terminal: true, latestSequence: 0,
          terminalEnvelope: {
            schemaVersion: 'moonmind.bridge-session-terminal.v1', status: 'failed',
            failureClass: 'configuration_error', failureCode: 'profile_missing',
            summary: 'Provider profile is unavailable.', diagnosticsRef: 'artifact:diagnostics',
            initialSnapshotRef: 'artifact:initial', rawEventsRef: 'artifact:raw',
            externalStateRef: 'artifact:external', cleanupState: 'completed',
            leaseReleaseState: 'released', evidenceIncompleteReason: null,
          },
        }) } as Response);
      }
      if (url.includes('/artifacts')) return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    await waitFor(() => expect(screen.getAllByText(/Provider profile is unavailable/).length).toBeGreaterThan(0));
    expect(screen.getAllByText(/Failure class: configuration_error/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Reason: profile_missing/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Cleanup: completed/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Lease release: released/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/verify the provider profile, credentials, and execution authorization/i).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: 'Open initial snapshot' }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: 'Open raw events' }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: 'Open external state' }).length).toBeGreaterThan(0);
    expect(screen.getByTestId('chat-session-blocks')).toBeTruthy();
  });

  it('uses advertised bridge capabilities and exposes delivery-unknown messages', async () => {
    window.history.pushState({}, 'Bridge Controls Test', '/workflows/test-123/chat?source=temporal');
    const priorEventSource = window.EventSource;
    window.EventSource = MockEventSource as unknown as typeof EventSource;
    const mockExecution = {
      taskId: 'test-123', workflowId: 'test-123', source: 'temporal', namespace: 'default',
      title: 'Bridge controls', summary: 'Running', createdAt: '2026-07-09T00:00:00Z',
      updatedAt: '2026-07-09T00:00:30Z', status: 'running', state: 'executing', rawState: 'running', actions: {},
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/omnigent/bridge-sessions/resolve')) return Promise.resolve({ ok: true, json: async () => ({
        bridgeSessionId: 'brs-controls', status: 'running', providerSessionRef: 'provider-session',
        capabilities: { sendFollowUp: true, interruptTurn: true },
      }) } as Response);
      if (url.includes('/omnigent/bridge-sessions/brs-controls/events')) return Promise.resolve({ ok: true, json: async () => ({
        schemaVersion: 'moonmind.bridge-session-events-page.v1', bridgeSessionId: 'brs-controls', items: [],
        after: 0, nextCursor: null, hasMore: false, terminal: false, latestSequence: 0,
      }) } as Response);
      if (url.includes('/omnigent/v1/sessions/provider-session/events')) return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
      if (url.includes('/artifacts')) return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    try {
      renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);
      const input = await screen.findByLabelText('Follow-up message');
      fireEvent.change(input, { target: { value: 'Continue with the fix.' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send follow-up' }));
      await waitFor(() => expect(screen.getByText(/Delivery confirmation pending/)).toBeTruthy());
      expect(screen.getByRole('button', { name: 'Interrupt turn' })).toBeTruthy();
      expect(fetchSpy.mock.calls.some(([url, init]) =>
        String(url).includes('/omnigent/v1/sessions/provider-session/events') &&
        String((init as RequestInit | undefined)?.body).includes('clientEventKey'))).toBe(true);
    } finally {
      window.EventSource = priorEventSource;
    }
  });

  it('shows failed bridge delivery and denies controls not advertised by the server', async () => {
    window.history.pushState({}, 'Bridge Failed Controls Test', '/workflows/test-123/chat?source=temporal');
    const priorEventSource = window.EventSource;
    window.EventSource = MockEventSource as unknown as typeof EventSource;
    const mockExecution = { taskId: 'test-123', workflowId: 'test-123', source: 'temporal', namespace: 'default', title: 'Bridge controls', summary: 'Running', createdAt: '2026-07-09T00:00:00Z', updatedAt: '2026-07-09T00:00:30Z', status: 'running', state: 'executing', rawState: 'running', actions: {} };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/bridge-sessions/resolve')) return Promise.resolve({ ok: true, json: async () => ({ bridgeSessionId: 'brs-fail', status: 'running', providerSessionRef: 'provider-session', capabilities: { sendFollowUp: true, interruptTurn: false } }) } as Response);
      if (url.includes('/bridge-sessions/brs-fail/events')) return Promise.resolve({ ok: true, json: async () => ({ schemaVersion: 'moonmind.bridge-session-events-page.v1', bridgeSessionId: 'brs-fail', items: [], after: 0, nextCursor: null, hasMore: false, terminal: false, latestSequence: 0 }) } as Response);
      if (url.includes('/omnigent/v1/sessions/provider-session/events')) return Promise.resolve({ ok: false, status: 403, json: async () => ({ detail: 'not authorized' }) } as Response);
      if (url.includes('/artifacts')) return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });
    try {
      renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);
      fireEvent.change(await screen.findByLabelText('Follow-up message'), { target: { value: 'Continue.' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send follow-up' }));
      await waitFor(() => expect(screen.getByText(/Operator message · Failed/)).toBeTruthy());
      expect(screen.queryByRole('button', { name: 'Interrupt turn' })).toBeNull();
      expect(screen.getByRole('region', { name: 'Bridge session controls' })).toBeTruthy();
    } finally {
      window.EventSource = priorEventSource;
    }
  });

  it('executes advertised bridge elicitation, clear, and cancel controls and preserves durable outcomes', async () => {
    window.history.pushState({}, 'Bridge Intervention Test', '/workflows/test-123/chat?source=temporal');
    const priorEventSource = window.EventSource;
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    window.EventSource = MockEventSource as unknown as typeof EventSource;
    const mockExecution = { taskId: 'test-123', workflowId: 'test-123', source: 'temporal', namespace: 'default', title: 'Bridge interventions', summary: 'Running', createdAt: '2026-07-09T00:00:00Z', updatedAt: '2026-07-09T00:00:30Z', status: 'running', state: 'executing', rawState: 'running', actions: {} };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/bridge-sessions/resolve')) return Promise.resolve({ ok: true, json: async () => ({
        bridgeSessionId: 'brs-interventions', status: 'running', providerSessionRef: 'provider-session',
        compatibilityProfile: 'omnigent.embedded.v1',
        providerProfileId: 'codex-profile', executionProfileRef: 'codex-default@2', launchPolicyRef: 'restricted@3',
        hostMode: 'on_demand_docker', effectiveLaunchSnapshotRef: 'omnigent-launch:sha256:safe-ref',
        hostLeaseRef: 'host-lease-1', credentialGeneration: 4,
        workflowId: 'test-123', runId: 'run-1', stepExecutionId: 'step-1', agentRunId: 'agent-run-1',
        firstMessageState: 'first_message_posted', omnigentHostRef: 'host-1', omnigentRunnerRef: 'runner-1',
        capabilities: { resolveElicitation: true, clearSession: true, cancelSession: true, stop: true, terminalCleanup: true },
      }) } as Response);
      if (url.includes('/bridge-sessions/brs-interventions/events')) return Promise.resolve({ ok: true, json: async () => ({
        schemaVersion: 'moonmind.bridge-session-events-page.v1', bridgeSessionId: 'brs-interventions',
        items: [
          { sequence: 1, timestamp: '2026-07-09T00:00:01Z', stream: 'session', kind: 'approval_requested', text: 'Allow the provider action?', metadata: { elicitationId: 'el-pending' } },
          { sequence: 2, timestamp: '2026-07-09T00:00:02Z', stream: 'session', kind: 'approval_requested', text: 'Previously resolved request.', metadata: { elicitationId: 'el-resolved' } },
          { sequence: 3, timestamp: '2026-07-09T00:00:03Z', stream: 'session', kind: 'approval_resolved', text: 'Previously approved by operator.', metadata: { elicitationId: 'el-resolved' } },
        ], after: 0, nextCursor: '3', hasMore: false, terminal: false, latestSequence: 3,
      }) } as Response);
      if (url.includes('/omnigent/v1/sessions/provider-session/')) return Promise.resolve({ ok: true, json: async () => ({ ok: true }) } as Response);
      if (url.includes('/artifacts')) return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });
    try {
      renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);
      expect(await screen.findByRole('region', { name: 'Pending operator request el-pending' })).toBeTruthy();
      const identity = screen.getByRole('region', { name: 'Omnigent runtime identity' });
      expect(identity.textContent).toContain('Codex via Omnigent');
      expect(identity.textContent).toContain('codex-profile');
      expect(identity.textContent).toContain('omnigent-launch:sha256:safe-ref');
      expect(screen.getAllByText('Previously approved by operator.').length).toBeGreaterThan(0);
      expect(screen.queryByRole('region', { name: 'Pending operator request el-resolved' })).toBeNull();

      fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
      await waitFor(() => expect(fetchSpy.mock.calls.some(([url, init]) =>
        String(url).endsWith('/omnigent/v1/sessions/provider-session/elicitations/el-pending/resolve') &&
        JSON.parse(String((init as RequestInit).body)).decision === 'approved')).toBe(true));
      fireEvent.click(screen.getByRole('button', { name: 'Clear session' }));
      await waitFor(() => expect(fetchSpy.mock.calls.some(([url, init]) =>
        String(url).endsWith('/omnigent/v1/sessions/provider-session/events') &&
        JSON.parse(String((init as RequestInit).body)).type === 'clear_session')).toBe(true));
      fireEvent.click(screen.getByRole('button', { name: 'Cancel session' }));
      await waitFor(() => expect(fetchSpy.mock.calls.some(([url, init]) =>
        String(url).endsWith('/omnigent/v1/sessions/provider-session/events') &&
        JSON.parse(String((init as RequestInit).body)).type === 'session.cancel')).toBe(true));
      fireEvent.click(screen.getByRole('button', { name: 'Stop session' }));
      await waitFor(() => expect(fetchSpy.mock.calls.some(([url, init]) =>
        String(url).endsWith('/omnigent/v1/sessions/provider-session/events') &&
        JSON.parse(String((init as RequestInit).body)).type === 'stop_session' &&
        JSON.parse(String((init as RequestInit).body)).expectedBridgeSessionId === 'brs-interventions' &&
        JSON.parse(String((init as RequestInit).body)).expectedRunnerId === 'runner-1' &&
        typeof JSON.parse(String((init as RequestInit).body)).idempotencyKey === 'string')).toBe(true));
      fireEvent.click(screen.getByRole('button', { name: 'Remove owned session' }));
      await waitFor(() => expect(fetchSpy.mock.calls.some(([url, init]) =>
        String(url).endsWith('/omnigent/v1/sessions/provider-session/events') &&
        JSON.parse(String((init as RequestInit).body)).type === 'cleanup_session' &&
        JSON.parse(String((init as RequestInit).body)).expectedWorkflowId === 'test-123')).toBe(true));
    } finally {
      confirmSpy.mockRestore();
      window.EventSource = priorEventSource;
    }
  });

  it('does not expose bridge elicitation, clear, or cancel actions unless advertised', async () => {
    window.history.pushState({}, 'Bridge Denied Intervention Test', '/workflows/test-123/chat?source=temporal');
    const priorEventSource = window.EventSource;
    window.EventSource = MockEventSource as unknown as typeof EventSource;
    const mockExecution = { taskId: 'test-123', workflowId: 'test-123', source: 'temporal', namespace: 'default', title: 'Bridge interventions', summary: 'Running', createdAt: '2026-07-09T00:00:00Z', updatedAt: '2026-07-09T00:00:30Z', status: 'running', state: 'executing', rawState: 'running', actions: {} };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/bridge-sessions/resolve')) return Promise.resolve({ ok: true, json: async () => ({ bridgeSessionId: 'brs-denied', status: 'running', providerSessionRef: 'provider-session', capabilities: {} }) } as Response);
      if (url.includes('/bridge-sessions/brs-denied/events')) return Promise.resolve({ ok: true, json: async () => ({ schemaVersion: 'moonmind.bridge-session-events-page.v1', bridgeSessionId: 'brs-denied', items: [{ sequence: 1, timestamp: '2026-07-09T00:00:01Z', stream: 'session', kind: 'approval_requested', text: 'Request without capability.', metadata: { elicitationId: 'el-denied' } }], after: 0, nextCursor: '1', hasMore: false, terminal: false, latestSequence: 1 }) } as Response);
      if (url.includes('/artifacts')) return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });
    try {
      renderWithClient(<WorkflowDetailPage payload={mockPayload} />);
      expect((await screen.findAllByText('Request without capability.')).length).toBeGreaterThan(0);
      expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Clear session' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Cancel session' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Stop session' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Remove owned session' })).toBeNull();
    } finally {
      window.EventSource = priorEventSource;
    }
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
          targetRuntime: 'claude_code',
          model: 'claude-sonnet-test',
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
      expect(screen.getByText('Claude Code')).toBeTruthy();
      expect(screen.getByText('claude-sonnet-test')).toBeTruthy();
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
      expect(screen.getByRole('link', { name: 'Execution' }).getAttribute('aria-current')).toBe('page');
      expect(screen.getByRole('link', { name: 'Overview' }).getAttribute('href')).toBe('/workflows/test-123/overview?source=temporal');
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

    const menu = await openWorkflowActionsMenu('Pause');
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

    let menu = await openWorkflowActionsMenu('Send Message');
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

    menu = await openWorkflowActionsMenu('Reject');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Reject' }));
    expect(screen.queryByRole('dialog')).toBeNull();

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/executions/test-123/cancel',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            action: 'reject',
            graceful: true,
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

    const menu = await openWorkflowActionsMenu('Cancel');
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
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: false,
              liveStreamStatus: 'unavailable',
              sessionSnapshot: {
                sessionId: 'sess:wf-task-1:codex_cli',
                sessionEpoch: 2,
                containerId: 'ctr-1',
                threadId: 'thread-2',
                activeTurnId: null,
              },
              interventionCapabilities: {
                sendFollowUp: true,
                clearSession: true,
                interruptTurn: false,
                cancelSession: false,
              },
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ events: [], truncated: false }),
        } as Response);
      }
      if (url.includes('/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => '' } as Response);
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
      if (url.includes('/sessions/sess%3Awf-task-1%3Acodex_cli/resources')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            agent_run_id: 'wf-task-1',
            session_id: 'sess:wf-task-1:codex_cli',
            session_epoch: 2,
            resources: [
              {
                resource_id: 'art-summary',
                artifact_id: 'art-summary',
                group_key: 'continuity',
                group_title: 'Continuity',
                label: 'summary.json',
                content_url: '/api/sessions/sess:wf-task-1:codex_cli/resources/art-summary/content',
                download_url: '/api/sessions/sess:wf-task-1:codex_cli/resources/art-summary/download',
                metadata: { filename: 'summary.json' },
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
      expect(screen.getByText('Resource Evidence')).toBeTruthy();
      expect(screen.getByText('summary.json')).toBeTruthy();
      expect(screen.getByText('Live Logs')).toBeTruthy();
      expect(screen.getByText('Diagnostics')).toBeTruthy();
    });
  });

  it('does not derive Session Continuity controls from Codex runtime names without a session snapshot', async () => {
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
      title: 'Codex one-shot task',
      summary: 'One-shot managed run',
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
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: false,
              liveStreamStatus: 'unavailable',
              sessionSnapshot: null,
              interventionCapabilities: {
                sendFollowUp: true,
                clearSession: true,
                interruptTurn: true,
                cancelSession: true,
              },
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ events: [], truncated: false }),
        } as Response);
      }
      if (url.includes('/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => '' } as Response);
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
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining('/observability-summary'),
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Session Continuity' })).toBeNull();
    });
    expect(
      fetchSpy.mock.calls.some(([input]) => String(input).includes('/artifact-sessions/')),
    ).toBe(false);
  });

  it('explains Live Logs as timeline history and Session Continuity as durable drill-down evidence', async () => {
    const codexPayload: BootPayload = {
      ...mockPayload,
      initialData: {
        dashboardConfig: {
          features: {
            temporalDashboard: { actionsEnabled: true },
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
              sessionSnapshot: {
                sessionId: 'sess:wf-task-1:codex_cli',
                sessionEpoch: 2,
                containerId: 'ctr-1',
                threadId: 'thread-2',
                activeTurnId: null,
              },
              interventionCapabilities: {
                sendFollowUp: false,
                clearSession: false,
                interruptTurn: false,
                cancelSession: false,
              },
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
    const liveLogsSummary = (await screen.findByText('Live Logs')).closest('summary');
    expect(liveLogsSummary).toBeTruthy();
    fireEvent.click(liveLogsSummary!);

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

    let resolveFollowUpControl: ((response: Response) => void) | null = null;

    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: false,
              liveStreamStatus: 'unavailable',
              sessionSnapshot: {
                sessionId: 'sess:wf-task-1:codex_cli',
                sessionEpoch: 1,
                containerId: 'ctr-1',
                threadId: 'thread-1',
                activeTurnId: null,
              },
              interventionCapabilities: {
                sendFollowUp: true,
                clearSession: true,
                interruptTurn: false,
                cancelSession: false,
              },
            },
          }),
        } as Response);
      }
      if (url.includes('/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control')) {
        const action = JSON.parse(String(init?.body || '{}')).action;
        if (action === 'continue_same_session') {
          return new Promise((resolve) => {
            resolveFollowUpControl = resolve;
          });
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({
            action,
            projection: {
              agent_run_id: 'wf-task-1',
              session_id: 'sess:wf-task-1:codex_cli',
              session_epoch: action === 'clear_session' ? 2 : 1,
              grouped_artifacts: [],
              latest_summary_ref: { artifact_id: 'art-summary' },
              latest_checkpoint_ref: { artifact_id: 'art-checkpoint' },
              latest_control_event_ref: { artifact_id: 'art-control' },
              latest_reset_boundary_ref:
                action === 'clear_session'
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
    const summaryCallsBeforeControl = fetchSpy.mock.calls.filter(([input]) =>
      String(input).includes('/observability-summary'),
    ).length;
    fireEvent.click(screen.getByRole('button', { name: 'Continue session' }));

    const pendingMessages = await screen.findByLabelText('Pending session messages');
    const optimisticBubble = within(pendingMessages).getByText('Continue with the existing session.');
    const optimisticContainer = optimisticBubble.closest('.chat-session-message');
    expect(optimisticContainer?.getAttribute('data-client-event-key')).toMatch(
      /^MM-1015:MM-977:sess:wf-task-1:codex_cli:1:\d+$/,
    );
    expect(screen.getByText('Operator message · Sending')).toBeTruthy();

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/agent-runs/wf-task-1/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"action":"continue_same_session"'),
        }),
      );
    });
    expect(resolveFollowUpControl).toBeTruthy();

    act(() => {
      resolveFollowUpControl?.({
        ok: true,
        json: async () => ({
          action: 'continue_same_session',
          projection: {
            agent_run_id: 'wf-task-1',
            session_id: 'sess:wf-task-1:codex_cli',
            session_epoch: 1,
            grouped_artifacts: [],
            latest_summary_ref: { artifact_id: 'art-summary' },
            latest_checkpoint_ref: { artifact_id: 'art-checkpoint' },
            latest_control_event_ref: { artifact_id: 'art-control' },
            latest_reset_boundary_ref: null,
          },
        }),
      } as Response);
    });
    await waitFor(() => {
      expect(screen.queryByText('Operator message · Sending')).toBeNull();
    });
    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.filter(([input]) => String(input) === executionDetailUrl).length,
      ).toBeGreaterThan(1);
    });
    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.filter(([input]) => String(input).includes('/observability-summary')).length,
      ).toBeGreaterThan(summaryCallsBeforeControl);
    });

    fireEvent.click(screen.getByRole('button', { name: 'Clear / Reset' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/agent-runs/wf-task-1/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"action":"clear_session"'),
        }),
      );
    });
  });

  it('marks optimistic follow-up bubbles failed when durable control confirmation fails', async () => {
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

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: false,
              liveStreamStatus: 'unavailable',
              sessionSnapshot: {
                sessionId: 'sess:wf-task-1:codex_cli',
                sessionEpoch: 1,
                containerId: 'ctr-1',
                threadId: 'thread-1',
                activeTurnId: null,
              },
              interventionCapabilities: {
                sendFollowUp: true,
                clearSession: false,
                interruptTurn: false,
                cancelSession: false,
              },
            },
          }),
        } as Response);
      }
      if (url.includes('/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control')) {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: async () => ({ detail: 'Follow-up is not supported for this session.' }),
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
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockExecution,
      } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={codexPayload} />);

    fireEvent.change(await screen.findByLabelText('Follow-up message'), {
      target: { value: 'Try the next turn.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Continue session' }));

    const pendingMessages = await screen.findByLabelText('Pending session messages');
    expect(within(pendingMessages).getByText('Try the next turn.')).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText('Operator message · Failed')).toBeTruthy();
    });
    expect(screen.getAllByText('Follow-up is not supported for this session.').length).toBeGreaterThan(0);
  });

  it('routes active-turn interrupt and cancel controls through the agent-run session control API', async () => {
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

    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: false,
              liveStreamStatus: 'unavailable',
              sessionSnapshot: {
                sessionId: 'sess:wf-task-1:codex_cli',
                sessionEpoch: 1,
                containerId: 'ctr-1',
                threadId: 'thread-1',
                activeTurnId: 'turn-1',
              },
              interventionCapabilities: {
                sendFollowUp: false,
                clearSession: true,
                interruptTurn: true,
                cancelSession: true,
              },
            },
          }),
        } as Response);
      }
      if (url.includes('/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            action: JSON.parse(String(init?.body || '{}')).action,
            projection: {
              agent_run_id: 'wf-task-1',
              session_id: 'sess:wf-task-1:codex_cli',
              session_epoch: 1,
              grouped_artifacts: [],
              latest_summary_ref: null,
              latest_checkpoint_ref: null,
              latest_control_event_ref: { artifact_id: 'art-control' },
              latest_reset_boundary_ref: null,
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

    fireEvent.click(await screen.findByRole('button', { name: 'Interrupt turn' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/agent-runs/wf-task-1/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"action":"interrupt_turn"'),
        }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: 'Cancel session' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/agent-runs/wf-task-1/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"action":"cancel_session"'),
        }),
      );
    });
  });

  it('handles Escape as a supported stop action and exposes disabled reasons for compact chat controls', async () => {
    window.history.pushState({}, 'Test', '/workflows/test-123?source=temporal');
    const codexPayload: BootPayload = {
      ...mockPayload,
      initialData: {
        dashboardConfig: {
          features: {
            temporalDashboard: { actionsEnabled: true },
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
      status: 'running',
      state: 'executing',
      rawState: 'executing',
      targetRuntime: 'codex_cli',
      agentRunId: 'wf-task-1',
      createdAt: '2026-03-28T00:00:00Z',
      updatedAt: '2026-03-28T00:00:02Z',
      actions: {},
    };
    let capabilities = {
      sendFollowUp: false,
      clearSession: false,
      interruptTurn: true,
      cancelSession: true,
    };

    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: false,
              liveStreamStatus: 'unavailable',
              sessionSnapshot: {
                sessionId: 'sess:wf-task-1:codex_cli',
                sessionEpoch: 1,
                containerId: 'ctr-1',
                threadId: 'thread-1',
                activeTurnId: 'turn-1',
              },
              interventionCapabilities: capabilities,
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({ ok: true, json: async () => ({ events: [], truncated: false }) } as Response);
      }
      if (url.includes('/logs/merged')) {
        return Promise.resolve({ ok: true, text: async () => '' } as unknown as Response);
      }
      if (url.includes('/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            action: JSON.parse(String(init?.body || '{}')).action,
            projection: {
              agent_run_id: 'wf-task-1',
              session_id: 'sess:wf-task-1:codex_cli',
              session_epoch: 1,
              grouped_artifacts: [],
              latest_summary_ref: null,
              latest_checkpoint_ref: null,
              latest_control_event_ref: { artifact_id: 'art-control' },
              latest_reset_boundary_ref: null,
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
            latest_summary_ref: null,
            latest_checkpoint_ref: null,
            latest_control_event_ref: null,
            latest_reset_boundary_ref: null,
          }),
        } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => mockExecution } as Response);
    });

    renderWithClient(<WorkflowDetailPage payload={codexPayload} />);

    const followUp = await screen.findByLabelText('Follow-up message');
    expect((screen.getByRole('button', { name: 'Continue session' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByText('Follow-up is not supported for this session.').length).toBeGreaterThan(0);
    expect((screen.getByRole('button', { name: 'Clear / Reset' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Clear / Reset is not supported for this session.')).toBeTruthy();

    fireEvent.keyDown(followUp, { key: 'Escape' });
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/agent-runs/wf-task-1/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"action":"interrupt_turn"'),
        }),
      );
    });

    fetchSpy.mockClear();
    capabilities = {
      sendFollowUp: false,
      clearSession: false,
      interruptTurn: false,
      cancelSession: false,
    };
    cleanup();
    renderWithClient(<WorkflowDetailPage payload={codexPayload} />);
    fireEvent.keyDown(await screen.findByLabelText('Follow-up message'), { key: 'Escape' });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/control'))).toBe(false);
    expect((screen.getByRole('button', { name: 'Interrupt turn' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Interrupt turn is not supported for this session.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Cancel session' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Cancel session is not supported for this session.')).toBeTruthy();
  });

  it('keeps polling session continuity until a projection or terminal state exists', () => {
    expect(getSessionProjectionRefetchInterval(false, false, false)).toBe(5000);
    expect(getSessionProjectionRefetchInterval(false, true, false)).toBe(false);
    expect(getSessionProjectionRefetchInterval(false, false, true)).toBe(false);
    expect(getSessionProjectionRefetchInterval(true, false, false)).toBe(false);
  });

  it('keeps polling session capabilities until a managed session is visible or terminal', () => {
    expect(getSessionCapabilityRefetchInterval(false, false, false)).toBe(5000);
    expect(getSessionCapabilityRefetchInterval(false, true, false)).toBe(false);
    expect(getSessionCapabilityRefetchInterval(false, false, true)).toBe(false);
    expect(getSessionCapabilityRefetchInterval(true, false, false)).toBe(false);
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
    targetRuntime: 'claude_code',
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
    vi.spyOn(window, 'confirm').mockReturnValue(true);
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

    const liveLogsSummary = (await screen.findByText('Live Logs')).closest('summary');
    expect(liveLogsSummary).toBeTruthy();
    fireEvent.click(liveLogsSummary!);

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

  it('uses Virtuoso follow-output to stick at the bottom while allowing scroll escape', async () => {
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
                stream: 'session',
                kind: 'assistant_message',
                text: 'Initial assistant message.',
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

    await screen.findByTestId('chat-session-blocks');

    await waitForEventSourceInstance();
    const es = MockEventSource.instances.at(-1)!;
    act(() => es.triggerOpen());
    act(() =>
      es.triggerLogChunk({
        sequence: 2,
        stream: 'session',
        kind: 'assistant_message',
        text: 'Live assistant update.',
      }),
    );

    await waitFor(() => expect(screen.getByText('Live assistant update.')).toBeTruthy());
    const followOutput = virtuosoPropsSpy.mock.calls.at(-1)?.[0].followOutput;
    expect(followOutput?.(true)).toBe('smooth');
    expect(followOutput?.(false)).toBe(false);
    act(() =>
      es.triggerLogChunk({
        sequence: 3,
        stream: 'session',
        kind: 'assistant_message',
        text: 'Second live update.',
      }),
    );

    await waitFor(() => expect(screen.getByText('Second live update.')).toBeTruthy());
    expect(virtuosoPropsSpy.mock.calls.at(-1)?.[0].followOutput?.(false)).toBe(false);
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
    const isolatedExecution = {
      ...activeExecution,
      agentRunId: '550e8400-e29b-41d4-a716-446655440099',
    };
    mockFetchSequence(isolatedExecution, activeSummary, 'backup');
    renderWithClient(<WorkflowDetailPage payload={mockPayload} />);

    // Wait until the initial execute fetch finishes so task is loaded
    await waitFor(() => expect(screen.getByText('Active task')).toBeTruthy());

    const summaryCallsBeforeExpand = fetchSpy.mock.calls.filter(([input]) =>
      String(input).includes('/observability-summary'),
    ).length;
    expect(summaryCallsBeforeExpand).toBe(0);

    const liveLogsSummary = await screen.findByText(
      (_content, element) => element?.tagName.toLowerCase() === 'summary' && element.textContent?.trim() === 'Live Logs',
    );
    const liveLogsDetails = liveLogsSummary.closest('details');
    expect(liveLogsDetails?.hasAttribute('open')).toBe(false);
    fireEvent.click(liveLogsSummary);
    await waitFor(() => expect(liveLogsDetails?.hasAttribute('open')).toBe(true));
    expect(screen.getByLabelText('Wrap lines')).toBeTruthy();

    // Now the Live Logs panel should perform its own summary fetch.
    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.filter(([input]) => String(input).includes('/observability-summary')).length,
      ).toBeGreaterThan(summaryCallsBeforeExpand);
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

  it('renders standardized chat timeline event families with distinct treatments', async () => {
    const events = [
      {
        sequence: 1,
        timestamp: '2026-04-08T00:00:01Z',
        stream: 'session',
        kind: 'user_message_submitted',
        text: 'User asked for implementation.',
        turn_id: 'turn-1',
      },
      {
        sequence: 2,
        timestamp: '2026-04-08T00:00:02Z',
        stream: 'session',
        kind: 'assistant_message_delta',
        text: 'Assistant draft output.',
        turn_id: 'turn-1',
      },
      {
        sequence: 3,
        timestamp: '2026-04-08T00:00:03Z',
        stream: 'session',
        kind: 'assistant_message_completed',
        text: 'Assistant completed output.',
        turn_id: 'turn-1',
      },
      {
        sequence: 4,
        timestamp: '2026-04-08T00:00:04Z',
        stream: 'session',
        kind: 'assistant_message',
        text: 'Assistant full message.',
        turn_id: 'turn-1',
      },
      {
        sequence: 5,
        timestamp: '2026-04-08T00:00:05Z',
        stream: 'session',
        kind: 'tool_call_started',
        text: 'Tool call started.',
        turn_id: 'turn-1',
      },
      {
        sequence: 6,
        timestamp: '2026-04-08T00:00:06Z',
        stream: 'session',
        kind: 'tool_call_output',
        text: 'Tool call output.',
        turn_id: 'turn-1',
      },
      {
        sequence: 7,
        timestamp: '2026-04-08T00:00:07Z',
        stream: 'session',
        kind: 'tool_call_completed',
        text: 'Tool call completed.',
        turn_id: 'turn-1',
      },
      {
        sequence: 8,
        timestamp: '2026-04-08T00:00:08Z',
        stream: 'session',
        kind: 'tool_call_failed',
        text: 'Tool call failed.',
        turn_id: 'turn-1',
      },
      {
        sequence: 9,
        timestamp: '2026-04-08T00:00:09Z',
        stream: 'session',
        kind: 'intervention_requested',
        text: 'Operator intervention requested.',
        turn_id: 'turn-1',
      },
      {
        sequence: 10,
        timestamp: '2026-04-08T00:00:10Z',
        stream: 'session',
        kind: 'turn_started',
        text: 'Turn started.',
        turn_id: 'turn-1',
      },
      {
        sequence: 11,
        timestamp: '2026-04-08T00:00:11Z',
        stream: 'session',
        kind: 'turn_completed',
        text: 'Turn completed.',
        turn_id: 'turn-1',
      },
      {
        sequence: 12,
        timestamp: '2026-04-08T00:00:12Z',
        stream: 'session',
        kind: 'turn_failed',
        text: 'Turn failed.',
        turn_id: 'turn-2',
      },
      {
        sequence: 13,
        timestamp: '2026-04-08T00:00:13Z',
        stream: 'session',
        kind: 'turn_interrupted',
        text: 'Turn interrupted.',
        turn_id: 'turn-3',
      },
    ];

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
          json: async () => ({ events, truncated: false }),
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
      expect(screen.getByText('User asked for implementation.')).toBeTruthy();
      expect(screen.getByText('Assistant full message.')).toBeTruthy();
      expect(screen.getAllByText('Tool call failed.').length).toBeGreaterThan(0);
      expect(screen.getByText('Operator intervention requested.')).toBeTruthy();
      expect(screen.getByText('Turn interrupted.')).toBeTruthy();
    });

    const expectedRowTypes = new Map([
      ['user_message_submitted', 'user'],
      ['assistant_message', 'assistant'],
      ['tool_call_started', 'tool'],
      ['tool_call_failed', 'tool'],
      ['intervention_requested', 'approval'],
      ['turn_started', 'turn'],
      ['turn_completed', 'turn'],
      ['turn_failed', 'turn-failure'],
      ['turn_interrupted', 'turn-failure'],
    ]);

    for (const [kind, rowType] of expectedRowTypes) {
      expect(document.querySelector(`[data-kind="${kind}"]`)?.getAttribute('data-row-type')).toBe(rowType);
    }
    expect(document.querySelectorAll('[data-chat-block-type="assistant"]').length).toBeGreaterThan(0);
    expect(document.querySelectorAll('[data-chat-block-type="tool"]').length).toBeGreaterThan(0);
    expect(document.querySelector('[data-kind="tool_call_failed"]')?.getAttribute('data-row-type')).toBe('tool');
  });

  it('MM-1014 renders chat session blocks, updates paired rows, streams live rows through the same projection, and exposes raw timeline', async () => {
    const events = [
      {
        sequence: 1,
        timestamp: '2026-04-08T00:00:01Z',
        stream: 'session',
        kind: 'user_message_submitted',
        text: 'Please inspect the run.',
        turn_id: 'turn-mm-1014',
      },
      {
        sequence: 2,
        timestamp: '2026-04-08T00:00:02Z',
        stream: 'session',
        kind: 'assistant_message',
        text: 'I will inspect the run.',
        turn_id: 'turn-mm-1014',
      },
      {
        sequence: 3,
        timestamp: '2026-04-08T00:00:03Z',
        stream: 'session',
        kind: 'tool_call_started',
        text: 'exec_command: rg -n MM-1014',
        turn_id: 'turn-mm-1014',
        metadata: { toolCallId: 'tool-1', toolName: 'exec_command' },
      },
      {
        sequence: 4,
        timestamp: '2026-04-08T00:00:04Z',
        stream: 'session',
        kind: 'tool_call_output',
        text: 'MM-1014 evidence line',
        turn_id: 'turn-mm-1014',
        metadata: { toolCallId: 'tool-1' },
      },
      {
        sequence: 5,
        timestamp: '2026-04-08T00:00:05Z',
        stream: 'session',
        kind: 'approval_requested',
        text: 'Approval requested for command execution.',
        turn_id: 'turn-mm-1014',
        metadata: { requestId: 'approval-1' },
      },
      {
        sequence: 6,
        timestamp: '2026-04-08T00:00:06Z',
        stream: 'session',
        kind: 'approval_resolved',
        text: 'Approval resolved by operator.',
        turn_id: 'turn-mm-1014',
        metadata: { requestId: 'approval-1' },
      },
      {
        sequence: 7,
        timestamp: '2026-04-08T00:00:07Z',
        stream: 'session',
        kind: 'session_cleared',
        text: 'Session cleared before the next turn.',
        metadata: { controlEventRef: 'art-control' },
      },
    ];

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: true,
              liveStreamStatus: 'available',
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ events, truncated: false }),
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
      expect(screen.getByTestId('chat-session-viewer')).toBeTruthy();
      expect(screen.getByText('Please inspect the run.')).toBeTruthy();
      expect(screen.getByText('I will inspect the run.')).toBeTruthy();
      expect(screen.getAllByText((content) => content.includes('exec_command')).length).toBeGreaterThanOrEqual(2);
      expect(screen.getByText('Approval requested for command execution.')).toBeTruthy();
      expect(screen.getByText('Approval resolved by operator.')).toBeTruthy();
      expect(screen.getByText('Session cleared before the next turn.')).toBeTruthy();
    });

    expect(screen.getByText('Raw Timeline')).toBeTruthy();
    expect(screen.queryByTestId('live-logs-timeline-viewer')).toBeNull();
    fireEvent.click(screen.getByText('Raw Timeline'));
    await waitFor(() => expect(screen.getByTestId('live-logs-timeline-viewer')).toBeTruthy());

    await waitForEventSourceInstance();
    const es = MockEventSource.instances.at(-1)!;
    act(() => es.triggerOpen());
    act(() =>
      es.triggerLogChunk({
        sequence: 8,
        stream: 'session',
        kind: 'assistant_message',
        text: 'Live assistant update.',
        turn_id: 'turn-mm-1014-live',
      }),
    );

    await waitFor(() => expect(screen.getAllByText('Live assistant update.').length).toBeGreaterThan(0));
    expect(document.querySelectorAll('[data-chat-block-type="approval"]')).toHaveLength(2);
    expect(document.querySelectorAll('[data-chat-block-type="tool"]').length).toBeGreaterThan(0);
  });

  it('MM-1032 validates the MM-977 managed-session chat workflow across history, live controls, refresh, fallback, and one-shot suppression', async () => {
    const payload: BootPayload = {
      ...sessionTimelinePayload,
      initialData: {
        dashboardConfig: {
          features: {
            temporalDashboard: { actionsEnabled: true },
            logStreamingEnabled: true,
            liveLogsSessionTimelineEnabled: true,
          },
        },
      },
    };
    const sessionExecution = {
      ...activeExecution,
      title: 'MM-1032 managed session validation',
      targetRuntime: 'codex_cli',
      agentRunId: 'agent-run-mm-1032',
    };
    const oneShotExecution = {
      ...sessionExecution,
      title: 'MM-1032 one-shot validation',
      agentRunId: 'agent-run-mm-1032-one-shot',
    };
    const sessionId = 'sess:agent-run-mm-1032:codex_cli';
    const sessionEvents = [
      {
        sequence: 1,
        timestamp: '2026-04-08T00:00:01Z',
        stream: 'session',
        kind: 'user_message_submitted',
        text: 'Operator asks the managed session to inspect MM-1032.',
        session_id: sessionId,
        session_epoch: 1,
        turn_id: 'turn-mm-1032',
        metadata: { clientMessageId: 'operator-1' },
      },
      {
        sequence: 2,
        timestamp: '2026-04-08T00:00:02Z',
        stream: 'session',
        kind: 'assistant_message_delta',
        text: 'Inspecting',
        session_id: sessionId,
        session_epoch: 1,
        turn_id: 'turn-mm-1032',
        metadata: { responseId: 'response-1' },
      },
      {
        sequence: 3,
        timestamp: '2026-04-08T00:00:03Z',
        stream: 'session',
        kind: 'assistant_message_completed',
        text: 'Inspecting the durable chat workflow.',
        session_id: sessionId,
        session_epoch: 1,
        turn_id: 'turn-mm-1032',
        metadata: { responseId: 'response-1' },
      },
      {
        sequence: 4,
        timestamp: '2026-04-08T00:00:04Z',
        stream: 'session',
        kind: 'tool_call_started',
        text: 'Read workflow detail tests.',
        session_id: sessionId,
        session_epoch: 1,
        turn_id: 'turn-mm-1032',
        metadata: { toolCallId: 'tool-1', toolName: 'exec_command' },
      },
      {
        sequence: 5,
        timestamp: '2026-04-08T00:00:05Z',
        stream: 'session',
        kind: 'tool_call_output',
        text: 'workflow-detail.test.tsx contains managed-session coverage.',
        session_id: sessionId,
        session_epoch: 1,
        turn_id: 'turn-mm-1032',
        metadata: { toolCallId: 'tool-1', toolName: 'exec_command' },
      },
      {
        sequence: 6,
        timestamp: '2026-04-08T00:00:06Z',
        stream: 'session',
        kind: 'tool_call_completed',
        text: 'Read completed.',
        session_id: sessionId,
        session_epoch: 1,
        turn_id: 'turn-mm-1032',
        metadata: { toolCallId: 'tool-1', toolName: 'exec_command' },
      },
      {
        sequence: 7,
        timestamp: '2026-04-08T00:00:07Z',
        stream: 'session',
        kind: 'approval_requested',
        text: 'Approval requested for validation command.',
        session_id: sessionId,
        session_epoch: 1,
        turn_id: 'turn-mm-1032',
        metadata: { requestId: 'approval-1' },
      },
      {
        sequence: 8,
        timestamp: '2026-04-08T00:00:08Z',
        stream: 'session',
        kind: 'approval_resolved',
        text: 'Approval resolved for validation command.',
        session_id: sessionId,
        session_epoch: 1,
        turn_id: 'turn-mm-1032',
        metadata: { requestId: 'approval-1' },
      },
      {
        sequence: 9,
        timestamp: '2026-04-08T00:00:09Z',
        stream: 'session',
        kind: 'turn_completed',
        text: 'Managed session turn completed.',
        session_id: sessionId,
        session_epoch: 1,
        turn_id: 'turn-mm-1032',
      },
      {
        sequence: 10,
        timestamp: '2026-04-08T00:00:10Z',
        stream: 'session',
        kind: 'session_cleared',
        text: 'Session cleared by operator before the next turn.',
        session_id: sessionId,
        session_epoch: 2,
        metadata: { controlEventRef: 'art-control', resetBoundaryRef: 'art-reset' },
      },
    ];
    const liveEvent = {
      sequence: 11,
      timestamp: '2026-04-08T00:00:11Z',
      stream: 'session',
      kind: 'assistant_message',
      text: 'Live append matches durable replay after refresh.',
      session_id: sessionId,
      session_epoch: 2,
      turn_id: 'turn-mm-1032-live',
      metadata: { responseId: 'response-live' },
    };
    let currentExecution = sessionExecution;
    let durableEvents = [...sessionEvents];
    let historyRequests = 0;
    let useFallbackHistory = false;
    let resolveFollowUpControl: ((response: Response) => void) | null = null;

    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/observability-summary')) {
        const isOneShot = currentExecution.agentRunId === 'agent-run-mm-1032-one-shot';
        return Promise.resolve({
          ok: true,
          json: async () => ({
            summary: {
              status: 'running',
              supportsLiveStreaming: !isOneShot,
              liveStreamStatus: isOneShot ? 'unavailable' : 'available',
              sessionSnapshot: isOneShot
                ? null
                : {
                    sessionId,
                    sessionEpoch: 1,
                    containerId: 'ctr-mm-1032',
                    threadId: 'thread-mm-1032',
                    activeTurnId: 'turn-mm-1032',
                  },
              interventionCapabilities: {
                sendFollowUp: true,
                clearSession: true,
                interruptTurn: false,
                cancelSession: false,
              },
            },
          }),
        } as Response);
      }
      if (url.includes('/observability/events')) {
        historyRequests += 1;
        if (useFallbackHistory) {
          return Promise.resolve({ ok: true, json: async () => ({ events: [], truncated: false }) } as Response);
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({
            events: durableEvents,
            truncated: false,
            sessionSnapshot: {
              sessionId,
              sessionEpoch: 2,
              containerId: 'ctr-mm-1032',
              threadId: 'thread-mm-1032',
              activeTurnId: 'turn-mm-1032-live',
            },
          }),
        } as Response);
      }
      if (url.includes('/logs/merged')) {
        return Promise.resolve({
          ok: true,
          text: async () => '--- session ---\nMerged fallback line for MM-1032 durable replay.\n',
        } as Response);
      }
      if (url.includes('/artifact-sessions/') && url.includes('/control')) {
        const action = JSON.parse(String(init?.body || '{}')).action;
        if (action === 'continue_same_session') {
          return new Promise((resolve) => {
            resolveFollowUpControl = resolve;
          });
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({
            action,
            projection: {
              agent_run_id: 'agent-run-mm-1032',
              session_id: sessionId,
              session_epoch: action === 'clear_session' ? 2 : 1,
              grouped_artifacts: [],
              latest_summary_ref: { artifact_id: 'art-summary' },
              latest_checkpoint_ref: { artifact_id: 'art-checkpoint' },
              latest_control_event_ref: { artifact_id: 'art-control' },
              latest_reset_boundary_ref: action === 'clear_session' ? { artifact_id: 'art-reset' } : null,
            },
          }),
        } as Response);
      }
      if (url.includes('/artifact-sessions/')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            agent_run_id: 'agent-run-mm-1032',
            session_id: sessionId,
            session_epoch: 1,
            grouped_artifacts: [],
            latest_summary_ref: { artifact_id: 'art-summary' },
            latest_checkpoint_ref: { artifact_id: 'art-checkpoint' },
            latest_control_event_ref: { artifact_id: 'art-control' },
            latest_reset_boundary_ref: { artifact_id: 'art-reset' },
          }),
        } as Response);
      }
      if (url.includes('/sessions/')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            agent_run_id: 'agent-run-mm-1032',
            session_id: sessionId,
            session_epoch: 1,
            resources: [],
          }),
        } as Response);
      }
      if (url.includes('/artifacts?link_type=report.primary&latest_only=true')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      if (url.includes('/artifacts')) {
        return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => currentExecution } as Response);
    });

    const rendered = renderWithClient(<WorkflowDetailPage payload={payload} />);
    fireEvent.click(await screen.findByText('Live Logs'));

    await waitFor(() => {
      expect(screen.getByTestId('chat-session-viewer')).toBeTruthy();
      expect(screen.getByText('Operator asks the managed session to inspect MM-1032.')).toBeTruthy();
      expect(screen.getByText('Inspecting the durable chat workflow.')).toBeTruthy();
      expect(screen.getAllByText((content) => content.includes('exec_command')).length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('Approval requested for validation command.')).toBeTruthy();
      expect(screen.getByText('Approval resolved for validation command.')).toBeTruthy();
      expect(screen.getByText('Managed session turn completed.')).toBeTruthy();
      expect(screen.getByText('Session cleared by operator before the next turn.')).toBeTruthy();
    });
    expect(document.querySelectorAll('[data-chat-block-type="user"]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-chat-block-type="assistant"]').length).toBeGreaterThan(0);
    expect(document.querySelectorAll('[data-chat-block-type="tool"]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-chat-block-type="approval"]')).toHaveLength(2);
    expect(document.querySelectorAll('[data-chat-block-type="status"]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-chat-block-type="boundary"]')).toHaveLength(1);
    fireEvent.click(screen.getByText('Raw Timeline'));
    await waitFor(() => {
      expect(screen.getByTestId('live-logs-timeline-viewer')).toBeTruthy();
      expect(document.querySelector('[data-kind="session_cleared"]')).toBeTruthy();
    });

    await waitForEventSourceInstance();
    const es = MockEventSource.instances.at(-1)!;
    act(() => es.triggerOpen());
    act(() => es.triggerLogChunk(liveEvent));
    await waitFor(() => {
      expect(screen.getAllByText('Live append matches durable replay after refresh.').length).toBeGreaterThan(0);
    });

    fireEvent.change(await screen.findByLabelText('Follow-up message'), {
      target: { value: 'Continue with the MM-1032 session.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Continue session' }));
    await waitFor(() => {
      expect(
        within(screen.getByLabelText('Pending session messages')).getByText(
          'Continue with the MM-1032 session.',
        ),
      ).toBeTruthy();
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/agent-runs/agent-run-mm-1032/artifact-sessions/sess%3Aagent-run-mm-1032%3Acodex_cli/control',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"action":"continue_same_session"'),
        }),
      );
    });
    act(() => {
      resolveFollowUpControl?.({
        ok: true,
        json: async () => ({
          action: 'continue_same_session',
          projection: {
            agent_run_id: 'agent-run-mm-1032',
            session_id: sessionId,
            session_epoch: 1,
            grouped_artifacts: [],
            latest_summary_ref: { artifact_id: 'art-summary' },
            latest_checkpoint_ref: { artifact_id: 'art-checkpoint' },
            latest_control_event_ref: { artifact_id: 'art-control' },
            latest_reset_boundary_ref: null,
          },
        }),
      } as Response);
    });
    await waitFor(() => expect(screen.queryByText('Operator message · Sending')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Clear / Reset' }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/agent-runs/agent-run-mm-1032/artifact-sessions/sess%3Aagent-run-mm-1032%3Acodex_cli/control',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"action":"clear_session"'),
        }),
      );
    });

    durableEvents = [...sessionEvents, liveEvent];
    const historyRequestsBeforeRefresh = historyRequests;
    rendered.unmount();
    MockEventSource.reset();
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    fireEvent.click(await screen.findByText('Live Logs'));
    await waitFor(() => {
      expect(historyRequests).toBeGreaterThan(historyRequestsBeforeRefresh);
      expect(screen.getByText('Live append matches durable replay after refresh.')).toBeTruthy();
      expect(document.querySelectorAll('[data-chat-block-type="assistant"]').length).toBeGreaterThanOrEqual(2);
    });

    cleanup();
    useFallbackHistory = true;
    MockEventSource.reset();
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    fireEvent.click(await screen.findByText('Live Logs'));
    await waitFor(() => {
      expect(screen.getByText('Raw history fallback active')).toBeTruthy();
      expect(screen.getByText(/Merged fallback line for MM-1032 durable replay/)).toBeTruthy();
    });

    cleanup();
    useFallbackHistory = false;
    currentExecution = oneShotExecution;
    renderWithClient(<WorkflowDetailPage payload={payload} />);
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(expect.stringContaining('/observability-summary'), expect.anything());
      expect(screen.queryByRole('heading', { name: 'Session Continuity' })).toBeNull();
      expect(screen.queryByLabelText('Follow-up message')).toBeNull();
    });
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
    const activeDetailResponse = () => ({
      ok: true,
      json: async () => activeExecution,
    }) as Response;
    let attachedDetailReleased = false;
    let releaseAttachedDetail: () => void = () => {
      attachedDetailReleased = true;
    };
    const attachedDetailPromise = new Promise<Response>((resolve) => {
      releaseAttachedDetail = () => {
        attachedDetailReleased = true;
        resolve(activeDetailResponse());
      };
    });
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
      if (detailCalls === 1) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...activeExecution,
            agentRunId: undefined,
            updatedAt: '2026-03-28T00:00:00Z',
          }),
        } as Response);
      }
      return attachedDetailReleased ? Promise.resolve(activeDetailResponse()) : attachedDetailPromise;
    });

    renderWithClient(<WorkflowDetailPage payload={fastPollPayload} />);

    await waitFor(() => {
      expect(
        screen.getByText(/Waiting for managed runtime launch to create live logs/i),
      ).toBeTruthy();
    });

    releaseAttachedDetail();
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
