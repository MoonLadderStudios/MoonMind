import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../../boot/parseBootPayload';
import { renderWithClient, screen } from '../../utils/test-utils';
import { workflowWorkspaceRowFromDetail } from '../../lib/workflowWorkspaceList';
import { WorkflowWorkspaceSidebarPanel } from './WorkflowWorkspaceSidebar';

function payload(listEnabled: boolean): BootPayload {
  return {
    page: 'dashboard',
    apiBase: '/api',
    initialData: {
      dashboardConfig: {
        pollIntervalsMs: { list: 60_000 },
        features: { temporalDashboard: { listEnabled } },
      },
    },
  };
}

describe('WorkflowWorkspaceSidebarPanel', () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{ workflowId: 'wf-1', title: 'First workflow', state: 'completed' }] }),
    } as Response);
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('keeps a stable hook order when the list flag is toggled', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const search = new URLSearchParams();
      const view = renderWithClient(
        <WorkflowWorkspaceSidebarPanel payload={payload(true)} search={search} />,
      );
      expect(await screen.findByRole('link', { name: /First workflow/ })).toBeTruthy();

      view.rerender(<WorkflowWorkspaceSidebarPanel payload={payload(false)} search={search} />);
      expect(screen.queryByRole('complementary', { name: 'Workflow navigation' })).toBeNull();

      view.rerender(<WorkflowWorkspaceSidebarPanel payload={payload(true)} search={search} />);
      expect(await screen.findByRole('link', { name: /First workflow/ })).toBeTruthy();

      const hookErrors = consoleErrorSpy.mock.calls.filter((call) => /hooks/i.test(String(call[0])));
      expect(hookErrors).toEqual([]);
    } finally {
      consoleErrorSpy.mockRestore();
    }
  });

  it('matches the list status when a completed workflow has a failed objective', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          { workflowId: 'wf-failed', title: 'Failed objective', state: 'completed', objectiveOutcome: 'failed' },
          { workflowId: 'wf-done', title: 'Succeeded objective', state: 'completed', objectiveOutcome: 'succeeded' },
        ],
      }),
    } as Response);

    renderWithClient(<WorkflowWorkspaceSidebarPanel payload={payload(true)} search={new URLSearchParams()} />);

    const failedLink = await screen.findByRole('link', { name: /Failed objective/ });
    const failedIcon = failedLink.querySelector('[data-testid="workflow-workspace-sidebar-status-icon"]');
    expect(failedIcon?.getAttribute('aria-label')).toBe('Status: Failed');
    expect(failedIcon?.className).toContain('status-failed');

    const doneLink = screen.getByRole('link', { name: /Succeeded objective/ });
    const doneIcon = doneLink.querySelector('[data-testid="workflow-workspace-sidebar-status-icon"]');
    expect(doneIcon?.getAttribute('aria-label')).toBe('Status: Completed');
    expect(doneIcon?.className).toContain('status-completed');
  });

  it('applies the objective outcome to the pinned current workflow', async () => {
    renderWithClient(
      <WorkflowWorkspaceSidebarPanel
        payload={payload(true)}
        search={new URLSearchParams()}
        activeWorkflowId="wf-pinned"
        pinnedCurrentRow={workflowWorkspaceRowFromDetail({
          workflowId: 'wf-pinned',
          title: 'Pinned workflow',
          state: 'completed',
          objectiveOutcome: 'verification_blocked',
        })}
      />,
    );

    const pinnedLink = await screen.findByRole('link', { name: /Pinned workflow/ });
    const pinnedIcon = pinnedLink.querySelector('[data-testid="workflow-workspace-sidebar-status-icon"]');
    expect(pinnedIcon?.getAttribute('aria-label')).toBe('Status: Verification blocked');
    expect(pinnedIcon?.className).toContain('status-failed');
  });
});
