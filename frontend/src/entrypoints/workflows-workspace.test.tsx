import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient, screen } from '../utils/test-utils';
import { WorkflowsWorkspacePage } from './workflows-workspace';

vi.mock('./workflow-list', () => ({
  default: () => <div data-testid="workflow-list-page">Workflow list</div>,
}));

vi.mock('./workflow-detail', () => ({
  default: () => <div data-testid="workflow-detail-entrypoint">Workflow detail entrypoint</div>,
  WorkflowDetailPage: () => <div data-testid="workflow-detail-page">Workflow detail page</div>,
}));

vi.mock('../components/workflows/WorkflowWorkspaceShell', () => ({
  WorkflowWorkspaceShell: ({
    displayMode,
    children,
  }: {
    displayMode?: 'hidden' | 'sidebar' | 'table';
    children?: ReactNode;
  }) => (
    <div data-testid="workflow-workspace-shell" data-display-mode={displayMode ?? 'unset'}>
      Workflow workspace shell
      {children}
    </div>
  ),
}));

vi.mock('./workflow-start', () => ({
  default: () => <div data-testid="workflow-start-page">Create workflow</div>,
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
  beforeEach(() => {
    vi.restoreAllMocks();
    mockDesktopViewport(true);
    window.history.pushState({}, 'Workspace Test', '/workflows/test-123?source=temporal');
  });

  it.each([
    { listEnabled: false, workspaceShellEnabled: true },
    { listEnabled: true, workspaceShellEnabled: false },
  ])('honors disabled desktop workspace shell flags %#', async (temporalDashboard) => {
    renderWorkspace({
      page: 'dashboard',
      apiBase: '/api',
      initialData: {
        dashboardConfig: {
          features: { temporalDashboard },
        },
      },
    });

    expect(await screen.findByTestId('workflow-detail-entrypoint')).toBeTruthy();
    expect(screen.queryByTestId('workflow-workspace-shell')).toBeNull();
  });

  it('mounts the desktop workspace shell when runtime flags allow it', async () => {
    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    // The shell renders at once; the detail page fills it after its chunk loads.
    expect(screen.getByTestId('workflow-workspace-shell')).toBeTruthy();
    expect(await screen.findByTestId('workflow-detail-page')).toBeTruthy();
    expect(screen.queryByTestId('workflow-detail-entrypoint')).toBeNull();
  });

  it('keeps /workflows as the table primary surface', () => {
    window.history.pushState({}, 'Workspace Table Test', '/workflows');

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    expect(screen.getByTestId('workflow-list-page')).toBeTruthy();
    expect(screen.queryByTestId('workflow-workspace-shell')).toBeNull();
  });

  it('renders the create workflow route as the create surface', async () => {
    window.history.pushState({}, 'Workspace Create Test', '/workflows/new');

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    expect(await screen.findByTestId('workflow-start-page')).toBeTruthy();
    expect(screen.queryByTestId('workflow-list-page')).toBeNull();
    expect(screen.queryByTestId('workflow-workspace-shell')).toBeNull();
  });

  it('renders first-workflow resolution status in the workflow detail loading area', () => {
    window.history.pushState({}, 'Workspace Opening Test', '/workflows');

    renderWorkspace({
      page: 'dashboard',
      apiBase: '/api',
      initialData: {
        workflowListDisplayMode: 'sidebar',
        workflowListDisplayStatus: 'Opening first workflow...',
      },
    });

    expect(screen.getByRole('main', { name: 'Workflow detail' })).toBeTruthy();
    expect(screen.getByText('Opening first workflow...')).toBeTruthy();
    expect(screen.getByTestId('loading-placeholder-detail')).toBeTruthy();
    expect(screen.queryByTestId('workflow-list-page')).toBeNull();
  });

  it('passes the resolved hidden/sidebar mode into detail workspace composition', () => {
    renderWorkspace({
      page: 'dashboard',
      apiBase: '/api',
      initialData: {
        workflowListDisplayMode: 'hidden',
      },
    });

    expect(screen.getByTestId('workflow-workspace-shell').getAttribute('data-display-mode')).toBe('hidden');
  });
});
