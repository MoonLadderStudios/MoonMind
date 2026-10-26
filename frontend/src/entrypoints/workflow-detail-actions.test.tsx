import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { navigateTo } from '../lib/navigation';
import { renderWithClient } from '../utils/test-utils';
import { WorkflowDetailPage } from './workflow-detail';

// Consequential Workflow Detail actions: menu ordering and focus, and
// confirmation proportional to effect for the menu, checkpoint branches,
// remediation controls and managed-session controls.

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: {
    data?: Array<{ id: string }>;
    itemContent: (index: number, row: { id: string }) => ReactNode;
  }) => (
    <div data-testid="mock-virtuoso">
      {(props.data ?? []).map((row, index) => (
        <div key={row.id ?? index}>{props.itemContent(index, row)}</div>
      ))}
    </div>
  ),
}));

vi.mock('../lib/navigation', () => ({
  navigateTo: vi.fn(),
}));

const actionsPayload: BootPayload = {
  page: 'workflow-detail',
  apiBase: '/api',
  initialData: {
    dashboardConfig: { features: { temporalDashboard: { actionsEnabled: true } } },
  },
};

function baseExecution(overrides: Record<string, unknown> = {}) {
  return {
    taskId: 'test-123',
    workflowId: 'test-123',
    namespace: 'default',
    temporalRunId: '01-run',
    runId: '01-run',
    source: 'temporal',
    workflowType: 'MoonMind.UserWorkflow',
    title: 'Guarded task',
    summary: 'Execution summary',
    status: 'running',
    state: 'executing',
    rawState: 'executing',
    temporalStatus: 'running',
    createdAt: '2026-03-28T00:00:00Z',
    updatedAt: '2026-03-28T00:00:02Z',
    actions: {},
    relatedRuns: [],
    ...overrides,
  };
}

const completedExecution = baseExecution({
  title: 'Completed task',
  status: 'completed',
  state: 'completed',
  rawState: 'completed',
  temporalStatus: 'completed',
  closedAt: '2026-03-28T00:10:00Z',
  actions: {
    canForceCancel: true,
    canSetTitle: false,
    canPause: false,
    canCancel: false,
    disabledReasons: {
      canSetTitle: 'state_not_eligible',
      canPause: 'state_not_eligible',
      canCancel: 'state_not_eligible',
    },
  },
});

let fetchSpy: MockInstance;

type Route = (url: string, init?: RequestInit) => Promise<Response> | null;

function mockFetch(execution: Record<string, unknown>, route: Route = () => null) {
  fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const routed = route(url, init);
    if (routed) return routed;
    if (init?.method === 'POST') {
      return Promise.resolve({ ok: true, json: async () => ({ accepted: true }) } as Response);
    }
    if (url.includes('/steps')) {
      return Promise.resolve({ ok: true, json: async () => ({ steps: [] }) } as Response);
    }
    if (url.includes('/artifacts')) {
      return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) } as Response);
    }
    return Promise.resolve({ ok: true, json: async () => execution } as Response);
  });
}

function postCalls(suffix?: string) {
  return fetchSpy.mock.calls.filter(
    ([url, init]) =>
      (init as RequestInit | undefined)?.method === 'POST' &&
      (!suffix || String(url).endsWith(suffix)),
  );
}

async function openWorkflowActionsMenu(expectedItemName?: string) {
  fireEvent.click(await screen.findByRole('button', { name: 'Workflow actions' }));
  const menu = screen.getByRole('menu', { name: 'Workflow actions' });
  if (expectedItemName) {
    await within(menu).findByRole('menuitem', { name: expectedItemName });
  }
  return menu;
}

beforeEach(() => {
  vi.restoreAllMocks();
  window.history.pushState({}, 'Test', '/workflows/test-123/steps?source=temporal');
  window.sessionStorage.clear();
  window.localStorage.clear();
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  fetchSpy = vi.spyOn(window, 'fetch');
  vi.mocked(navigateTo).mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Workflow actions menu', () => {
  const runningExecution = baseExecution({
    actions: { canSetTitle: true, canPause: true, canCancel: true, canForceCancel: true },
  });

  it('lists destructive actions after the safe ones, behind a separator', async () => {
    mockFetch(runningExecution);
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);
    const menu = await openWorkflowActionsMenu('Force cancel');

    const names = within(menu)
      .getAllByRole('menuitem')
      .map((item) => item.getAttribute('aria-label') ?? item.textContent);
    expect(names.indexOf('Cancel')).toBeGreaterThan(names.indexOf('Pause'));
    expect(names.indexOf('Cancel')).toBeGreaterThan(names.indexOf('Rename'));
    expect(names.at(-1)).toBe('Force cancel');
    const separator = within(menu).getAllByRole('separator')[0]!;
    const pause = within(menu).getByRole('menuitem', { name: 'Pause' });
    const cancel = within(menu).getByRole('menuitem', { name: 'Cancel' });
    expect(pause.compareDocumentPosition(separator) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(separator.compareDocumentPosition(cancel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it.each([
    ['click', (trigger: HTMLElement) => fireEvent.click(trigger)],
    ['Enter', (trigger: HTMLElement) => fireEvent.keyDown(trigger, { key: 'Enter' })],
    ['Space', (trigger: HTMLElement) => fireEvent.keyDown(trigger, { key: ' ' })],
    ['ArrowDown', (trigger: HTMLElement) => fireEvent.keyDown(trigger, { key: 'ArrowDown' })],
  ] as const)('never puts initial focus on a destructive action when opened by %s', async (_how, open) => {
    mockFetch(completedExecution);
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);
    await screen.findByText('Completed task');
    open(await screen.findByRole('button', { name: 'Workflow actions' }));

    const menu = screen.getByRole('menu', { name: 'Workflow actions' });
    await waitFor(() => expect(menu.contains(document.activeElement)).toBe(true));
    const focused = document.activeElement as HTMLElement;
    expect(focused).not.toBe(within(menu).getByRole('menuitem', { name: 'Force cancel' }));
    expect(focused.className).not.toContain('danger');
    // With no enabled safe action, focus lands on the Unavailable toggle.
    expect(focused.getAttribute('aria-label') ?? focused.textContent).toMatch(/^Unavailable \(\d+\)/);
  });

  it.each([
    ['Cancel', 'Cancel workflow?', 'Cancel workflow', { action: 'cancel', graceful: true }],
    ['Reject', 'Reject workflow?', 'Reject workflow', { action: 'reject', graceful: true }],
    ['Force cancel', 'Force cancel workflow?', 'Force cancel', { action: 'cancel', graceful: false }],
  ] as const)('asks for confirmation before sending %s', async (label, title, confirmLabel, body) => {
    mockFetch(
      baseExecution({
        state: 'awaiting_external',
        rawState: 'awaiting_external',
        actions: { canCancel: true, canReject: true, canForceCancel: true, canResume: true },
      }),
    );
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);
    let menu = await openWorkflowActionsMenu(label);
    fireEvent.click(within(menu).getByRole('menuitem', { name: label }));

    const dialog = screen.getByRole('dialog', { name: title });
    expect(dialog.textContent).toMatch(/cannot be undone/i);
    expect(postCalls('/cancel')).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Go back' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(postCalls('/cancel')).toHaveLength(0);

    menu = await openWorkflowActionsMenu(label);
    fireEvent.click(within(menu).getByRole('menuitem', { name: label }));
    fireEvent.click(
      within(screen.getByRole('dialog', { name: title })).getByRole('button', { name: confirmLabel }),
    );
    await waitFor(() => {
      expect(postCalls('/cancel')).toHaveLength(1);
      expect(JSON.parse(String((postCalls('/cancel')[0]![1] as RequestInit).body))).toEqual(body);
    });
  });

  it('keeps Pause immediate because it is reversible', async () => {
    mockFetch(runningExecution);
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);
    const menu = await openWorkflowActionsMenu('Pause');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Pause' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(postCalls('/signal')).toHaveLength(1));
  });

  it('MoonLadderStudios/MoonMind#4870 refreshes the workflow list and detail after an action', async () => {
    mockFetch(runningExecution);
    const { queryClient } = renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const menu = await openWorkflowActionsMenu('Pause');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Pause' }));
    await waitFor(() => expect(postCalls('/signal')).toHaveLength(1));
    await waitFor(() => {
      const keys = invalidateSpy.mock.calls.map(([filters]) => filters?.queryKey?.[0]);
      expect(keys).toContain('workflow-list');
      expect(keys).toContain('workflow-detail');
    });
  });

  it('keeps Force cancel reachable under Troubleshooting on a completed workflow', async () => {
    mockFetch(completedExecution);
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);
    await openWorkflowActionsMenu('Force cancel');

    const troubleshooting = screen.getByRole('group', { name: 'Troubleshooting' });
    const forceCancel = within(troubleshooting).getByRole('menuitem', { name: 'Force cancel' });
    expect(forceCancel.getAttribute('aria-disabled')).toBeNull();
    expect(within(troubleshooting).getByText(/without cleanup/i)).toBeTruthy();

    fireEvent.click(forceCancel);
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Force cancel workflow?' })).getByRole('button', {
        name: 'Force cancel',
      }),
    );
    await waitFor(() => {
      expect(JSON.parse(String((postCalls('/cancel')[0]?.[1] as RequestInit).body))).toEqual({
        action: 'cancel',
        graceful: false,
      });
    });
  });

  it('collapses ineligible actions under Unavailable with plain-language reasons', async () => {
    mockFetch(completedExecution);
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);
    const menu = await openWorkflowActionsMenu('Force cancel');

    expect(within(menu).queryByRole('menuitem', { name: 'Pause' })).toBeNull();
    expect(within(menu).queryByText(/state not eligible/i)).toBeNull();
    const toggle = within(menu).getByRole('menuitem', { name: 'Unavailable (3)' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    const unavailable = within(menu).getByRole('group', { name: 'Unavailable actions' });
    for (const label of ['Cancel', 'Rename', 'Pause']) {
      const item = within(unavailable).getByRole('menuitem', { name: label });
      expect(item.getAttribute('aria-disabled')).toBe('true');
      expect(within(item).getByText("Not available in the workflow's current state.")).toBeTruthy();
    }
  });
});

describe('Checkpoint branch actions', () => {
  const stepsWithCheckpoint = {
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
        status: 'completed',
        waitingReason: null,
        attentionRequired: false,
        executionOrdinal: 1,
        startedAt: '2026-04-09T00:00:03Z',
        updatedAt: '2026-04-09T00:00:04Z',
        summary: 'Applied repository changes',
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
        stateCheckpointRef: 'artifact://checkpoint-apply',
      },
    ],
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
    artifactRefs: {},
    publishStatus: 'unpublished',
    createdAt: '2026-04-09T00:01:00Z',
    updatedAt: '2026-04-09T00:02:00Z',
  };

  it.each([
    ['Promote branch', 'Promote branch?', '/checkpoint-branches/branch-a/promote'],
    ['Publish branch', 'Publish branch?', '/checkpoint-branches/branch-a/publish'],
    ['Archive branch', 'Archive branch?', '/checkpoint-branches/branch-a/archive'],
  ] as const)('confirms %s before sending it', async (buttonName, title, endpoint) => {
    mockFetch(baseExecution({
      temporalRunId: '02-run',
      runId: '02-run',
      stepsHref: '/api/executions/test-123/steps',
    }), (url) => {
      if (url.endsWith('/checkpoint-branches/branch-a/turns')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      if (url.endsWith('/checkpoint-branches')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [branch] }) } as Response);
      }
      if (url.includes('/executions/test-123/steps')) {
        return Promise.resolve({ ok: true, json: async () => stepsWithCheckpoint } as Response);
      }
      return null;
    });
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: buttonName }));
    const dialog = screen.getByRole('dialog', { name: title });
    expect(dialog.textContent).toContain('Try minimal API contract fix');
    expect(postCalls(endpoint)).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Go back' }));
    expect(postCalls(endpoint)).toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: buttonName }));
    fireEvent.click(
      within(screen.getByRole('dialog', { name: title })).getByRole('button', { name: buttonName }),
    );
    await waitFor(() => expect(postCalls(endpoint)).toHaveLength(1));
  });
});

describe('Remediation operator controls', () => {
  it('confirms Cancel remediation but takes over without a dialog', async () => {
    window.history.pushState({}, 'Artifacts', '/workflows/test-123/artifacts?source=temporal');
    const remediation = {
      remediationWorkflowId: 'mm:remediation-1',
      remediationRunId: 'run-remediation-1',
      targetWorkflowId: 'test-123',
      targetRunId: '01-run',
      mode: 'snapshot_then_follow',
      authorityMode: 'approval_gated',
      status: 'running',
      operatorControls: {
        canCancel: true,
        canForceCancel: false,
        canTakeOver: true,
        canResume: false,
        paused: false,
        disabledReasons: {},
      },
    };
    mockFetch(
      baseExecution({ status: 'failed', state: 'failed', rawState: 'failed', temporalStatus: 'failed' }),
      (url) => {
        if (url.includes('/remediations?direction=inbound')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ direction: 'inbound', items: [remediation] }),
          } as Response);
        }
        if (url.includes('/remediations?direction=outbound')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ direction: 'outbound', items: [] }),
          } as Response);
        }
        return null;
      },
    );
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Take over and pause' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(postCalls('/executions/mm%3Aremediation-1/signal')).toHaveLength(1));

    fireEvent.click(screen.getByRole('button', { name: 'Cancel remediation' }));
    const dialog = screen.getByRole('dialog', { name: 'Cancel remediation?' });
    expect(postCalls('/executions/mm%3Aremediation-1/cancel')).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel remediation' }));
    await waitFor(() => {
      const [cancelCall] = postCalls('/executions/mm%3Aremediation-1/cancel');
      expect(JSON.parse(String((cancelCall?.[1] as RequestInit).body))).toMatchObject({
        action: 'cancel',
        graceful: true,
      });
    });
  });
});

describe('Managed session controls', () => {
  const sessionExecution = baseExecution({
    title: 'Codex session task',
    targetRuntime: 'codex_cli',
    agentRunId: 'wf-task-1',
    actions: { canCancel: true, canForceCancel: true },
  });
  const controlUrl =
    '/api/agent-runs/wf-task-1/artifact-sessions/sess%3Awf-task-1%3Acodex_cli/control';
  const projection = {
    agent_run_id: 'wf-task-1',
    session_id: 'sess:wf-task-1:codex_cli',
    session_epoch: 1,
    grouped_artifacts: [],
    latest_summary_ref: null,
    latest_checkpoint_ref: null,
    latest_control_event_ref: null,
    latest_reset_boundary_ref: null,
  };
  const sessionRoutes: Route = (url, init) => {
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
        json: async () => ({ action: JSON.parse(String(init?.body || '{}')).action, projection }),
      } as Response);
    }
    if (url.includes('/artifact-sessions/sess%3Awf-task-1%3Acodex_cli')) {
      return Promise.resolve({ ok: true, json: async () => projection } as Response);
    }
    return null;
  };
  const controlActions = () =>
    postCalls().filter(([url]) => String(url) === controlUrl)
      .map(([, init]) => JSON.parse(String((init as RequestInit).body)).action);

  it.each([
    ['Clear / Reset', 'Clear session?', 'Clear session', 'clear_session'],
    ['Cancel session', 'Cancel session?', 'Cancel session', 'cancel_session'],
  ] as const)('confirms %s in a dashboard dialog instead of window.confirm', async (buttonName, title, confirmLabel, action) => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    mockFetch(sessionExecution, sessionRoutes);
    renderWithClient(<WorkflowDetailPage payload={actionsPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: buttonName }));
    expect(confirmSpy).not.toHaveBeenCalled();
    const dialog = screen.getByRole('dialog', { name: title });
    expect(controlActions()).toEqual([]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Go back' }));
    expect(controlActions()).toEqual([]);

    fireEvent.click(screen.getByRole('button', { name: buttonName }));
    fireEvent.click(
      within(screen.getByRole('dialog', { name: title })).getByRole('button', { name: confirmLabel }),
    );
    await waitFor(() => expect(controlActions()).toEqual([action]));
    expect(confirmSpy).not.toHaveBeenCalled();
  });
});
