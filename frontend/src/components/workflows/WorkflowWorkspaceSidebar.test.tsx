import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../../boot/parseBootPayload';
import { renderWithClient, screen } from '../../utils/test-utils';
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
});
