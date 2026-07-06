import { beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import { fireEvent, waitFor } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient, screen } from '../utils/test-utils';
import { readDashboardPreferences, updateDashboardPreferences } from '../utils/dashboardPreferences';
import { WorkflowsWorkspacePage } from './workflows-workspace';

vi.mock('./workflow-list', () => ({
  default: () => <div data-testid="workflow-list-page">Workflow list</div>,
}));

vi.mock('./workflow-detail', () => ({
  default: () => <div data-testid="workflow-detail-entrypoint">Workflow detail entrypoint</div>,
  WorkflowWorkspaceShell: () => <div data-testid="workflow-workspace-shell">Workflow workspace shell</div>,
}));

function mockDesktopViewport(matches: boolean) {
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

function renderWorkspace(payload: BootPayload) {
  return renderWithClient(
    <BrowserRouter>
      <WorkflowsWorkspacePage payload={payload} />
    </BrowserRouter>,
  );
}

describe('WorkflowsWorkspacePage', () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    mockDesktopViewport(true);
    fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          { workflowId: 'workflow-1', title: 'First workflow' },
          { workflowId: 'workflow-2', title: 'Second workflow' },
        ],
      }),
    } as Response);
    window.history.pushState({}, 'Workspace Test', '/workflows/test-123?source=temporal');
  });

  it.each([
    { listEnabled: false, workspaceShellEnabled: true },
    { listEnabled: true, workspaceShellEnabled: false },
  ])('honors disabled desktop workspace shell flags %#', (temporalDashboard) => {
    renderWorkspace({
      page: 'dashboard',
      apiBase: '/api',
      initialData: {
        dashboardConfig: {
          features: { temporalDashboard },
        },
      },
    });

    expect(screen.getByTestId('workflow-detail-entrypoint')).toBeTruthy();
    expect(screen.queryByTestId('workflow-workspace-shell')).toBeNull();
  });

  it('mounts the desktop workspace shell when runtime flags allow it', () => {
    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    expect(screen.getByTestId('workflow-workspace-shell')).toBeTruthy();
    expect(screen.queryByTestId('workflow-detail-entrypoint')).toBeNull();
  });

  it('MM-1117 opens the persisted selected workflow from table mode when the row is authorized', async () => {
    window.history.pushState({}, 'Workflow Table', '/workflows?source=temporal&stateIn=completed&pageSize=50');
    updateDashboardPreferences({ lastSelectedWorkflowId: 'workflow-2' });

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    fireEvent.click(screen.getByRole('radio', { name: 'No list' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/workflow-2');
    });
    expect(readDashboardPreferences().workflowListDisplayMode).toBe('hidden');
    expect(fetchSpy).toHaveBeenCalledWith('/api/executions?source=temporal&stateIn=completed&pageSize=50');
    expect(window.location.search).not.toContain('workflowListDisplayMode');
  });

  it('MM-1117 opens the first visible workflow from the current list query when no selection exists', async () => {
    window.history.pushState({}, 'Workflow Table', '/workflows?source=temporal&repoContains=moon%2Frepo');

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    fireEvent.click(screen.getByRole('radio', { name: 'Sidebar list' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/workflow-1');
    });
    expect(readDashboardPreferences().workflowListDisplayMode).toBe('sidebar');
    expect(fetchSpy).toHaveBeenCalledWith('/api/executions?source=temporal&repoContains=moon%2Frepo&pageSize=25');
  });

  it('MM-1117 stays on table mode when hidden mode has no selectable workflow to open', async () => {
    window.history.pushState({}, 'Workflow Table', '/workflows?source=temporal&stateIn=failed');
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [] }),
    } as Response);

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    fireEvent.click(screen.getByRole('radio', { name: 'No list' }));

    expect(await screen.findByText('No workflow is available to open from this list.')).toBeTruthy();
    expect(window.location.pathname).toBe('/workflows');
    expect(readDashboardPreferences().workflowListDisplayMode).toBe('hidden');
  });

  it('MM-1117 reports unavailable navigation when table mode resolution fails', async () => {
    window.history.pushState({}, 'Workflow Table', '/workflows?source=temporal&stateIn=failed');
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => {
        throw new Error('invalid json');
      },
    } as unknown as Response);

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    fireEvent.click(screen.getByRole('radio', { name: 'Sidebar list' }));

    expect(await screen.findByText('Workflow navigation is unavailable.')).toBeTruthy();
    expect(window.location.pathname).toBe('/workflows');
    expect(readDashboardPreferences().workflowListDisplayMode).toBe('sidebar');
  });
});
