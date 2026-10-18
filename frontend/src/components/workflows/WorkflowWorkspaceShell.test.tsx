import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider, useQuery } from '@tanstack/react-query';

import type { BootPayload } from '../../boot/parseBootPayload';
import { createDashboardQueryClient } from '../../boot/queryClient';
import type { WorkflowWorkspaceRow } from '../../lib/workflowWorkspaceList';
import { workflowDetailQueryOptions } from '../../entrypoints/workflow-detail';
import { WorkflowWorkspaceShell } from './WorkflowWorkspaceShell';

vi.mock('./WorkflowWorkspaceSidebar', () => ({
  WorkflowWorkspaceSidebarPanel: ({
    pinnedCurrentRow,
  }: {
    pinnedCurrentRow: WorkflowWorkspaceRow | null;
  }) => <div data-testid="pinned-row">{pinnedCurrentRow?.title ?? 'none'}</div>,
}));

const payload: BootPayload = { page: 'workflows', apiBase: '/api' };

function terminalExecution(title: string) {
  return {
    taskId: 'wf-terminal',
    workflowId: 'wf-terminal',
    namespace: 'default',
    temporalRunId: '01-run',
    runId: '01-run',
    source: 'temporal',
    workflowType: 'MoonMind.UserWorkflow',
    title,
    summary: 'Execution summary',
    status: 'completed',
    state: 'completed',
    rawState: 'completed',
    temporalStatus: 'completed',
    createdAt: '2026-03-28T00:00:00Z',
    updatedAt: '2026-03-28T00:00:02Z',
    closedAt: '2026-03-28T00:10:00Z',
    actions: {},
    relatedRuns: [],
  };
}

// Stands in for the lazily loaded detail page: it owns the detail snapshot
// query exactly as WorkflowDetailPage does.
function DetailChild() {
  const query = useQuery(
    workflowDetailQueryOptions({
      apiBase: '/api',
      workflowId: 'wf-terminal',
      sourceTemporal: false,
      detailPoll: 2000,
    }),
  );
  return (
    <div>
      <span data-testid="detail-title">{query.data?.title ?? ''}</span>
      <span data-testid="detail-error">{query.error ? String(query.error) : ''}</span>
    </div>
  );
}

describe('WorkflowWorkspaceShell', () => {
  let fetchSpy: MockInstance;
  let title: string;

  beforeEach(() => {
    title = 'Before action';
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async () =>
      new Response(JSON.stringify(terminalExecution(title)), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('keeps the detail page able to refetch after an action invalidates the snapshot (#4870)', async () => {
    const queryClient = createDashboardQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <WorkflowWorkspaceShell
          payload={payload}
          workflowId="wf-terminal"
          search={new URLSearchParams()}
          displayMode="sidebar"
        >
          <DetailChild />
        </WorkflowWorkspaceShell>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('detail-title').textContent).toBe('Before action'));
    expect(screen.getByTestId('pinned-row').textContent).toBe('Before action');
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    // A terminal workflow does not poll, so only the action's invalidation
    // can refresh it.
    title = 'After action';
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['workflow-detail', 'wf-terminal'] });
    });

    await waitFor(() => expect(screen.getByTestId('detail-title').textContent).toBe('After action'));
    expect(screen.getByTestId('detail-error').textContent).toBe('');
    expect(screen.getByTestId('pinned-row').textContent).toBe('After action');
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(queryClient.getQueryState(['workflow-detail', 'wf-terminal', false])?.error).toBeNull();
  });
});
