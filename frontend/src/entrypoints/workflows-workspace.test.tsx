import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import type { ReactNode } from 'react';

import type { BootPayload } from '../boot/parseBootPayload';
import { fireEvent, renderWithClient, screen } from '../utils/test-utils';
import { WorkflowsWorkspacePage } from './workflows-workspace';

const { sidebarListFailure } = vi.hoisted(() => ({
  sidebarListFailure: { enabled: false },
}));

vi.mock('./workflow-list', () => ({
  default: () => <div data-testid="workflow-list-page">Workflow list</div>,
}));

vi.mock('./workflow-detail', () => ({
  default: () => <div data-testid="workflow-detail-entrypoint">Workflow detail entrypoint</div>,
  WorkflowWorkspaceShell: ({ displayMode }: { displayMode?: string }) => (
    <div data-testid="workflow-workspace-shell" data-display-mode={displayMode}>
      Workflow workspace shell
    </div>
  ),
  WorkflowWorkspaceSidebarLayout: ({
    children,
    onSidebarNavigate,
  }: {
    children: ReactNode;
    onSidebarNavigate?: ((href: string) => boolean) | undefined;
  }) => (
    <div data-testid="workflow-sidebar-layout">
      {sidebarListFailure.enabled ? <p role="alert">Sidebar list failed</p> : null}
      <a
        href="/workflows/sidebar-target?source=temporal"
        onClick={(event) => {
          if (onSidebarNavigate?.('/workflows/sidebar-target?source=temporal') === false) {
            event.preventDefault();
          }
        }}
      >
        Sidebar target workflow
      </a>
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
    sidebarListFailure.enabled = false;
    window.localStorage.clear();
    mockDesktopViewport(true);
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

  it('renders the workflows table route without hidden detail or create surfaces', () => {
    window.history.pushState({}, 'Workspace Test', '/workflows');

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    expect(screen.getByTestId('workflow-list-page')).toBeTruthy();
    expect(screen.queryByTestId('workflow-workspace-shell')).toBeNull();
    expect(screen.queryByTestId('workflow-start-page')).toBeNull();
  });

  it('renders hidden detail as the detail shell without the sidebar mode', () => {
    window.localStorage.setItem(
      'moonmind.dashboard.preferences',
      JSON.stringify({
        version: 1,
        preferences: {
          workflowListDisplayMode: 'hidden',
        },
      }),
    );

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    expect(screen.getByTestId('workflow-workspace-shell').getAttribute('data-display-mode')).toBe('hidden');
  });

  it('renders hidden create alone', () => {
    window.localStorage.setItem(
      'moonmind.dashboard.preferences',
      JSON.stringify({
        version: 1,
        preferences: {
          workflowListDisplayMode: 'hidden',
        },
      }),
    );
    window.history.pushState({}, 'Workspace Test', '/workflows/new');

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    expect(screen.getByTestId('workflow-start-page')).toBeTruthy();
    expect(screen.queryByTestId('workflow-sidebar-layout')).toBeNull();
  });

  it('renders sidebar create with workspace-owned navigation beside create', () => {
    window.localStorage.setItem(
      'moonmind.dashboard.preferences',
      JSON.stringify({
        version: 1,
        preferences: {
          workflowListDisplayMode: 'sidebar',
        },
      }),
    );
    window.history.pushState({}, 'Workspace Test', '/workflows/new');

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    expect(screen.getByTestId('workflow-sidebar-layout')).toBeTruthy();
    expect(screen.getByTestId('workflow-start-page')).toBeTruthy();
  });

  it('keeps Create usable when the workspace-owned sidebar list fails', () => {
    window.localStorage.setItem(
      'moonmind.dashboard.preferences',
      JSON.stringify({
        version: 1,
        preferences: {
          workflowListDisplayMode: 'sidebar',
        },
      }),
    );
    window.history.pushState({}, 'Workspace Test', '/workflows/new');
    sidebarListFailure.enabled = true;

    renderWorkspace({ page: 'dashboard', apiBase: '/api' });

    expect(screen.getByText('Sidebar list failed')).toBeTruthy();
    expect(screen.getByTestId('workflow-start-page')).toBeTruthy();
  });

  it('asks the Create route-change guard before sidebar row navigation leaves Create', () => {
    window.localStorage.setItem(
      'moonmind.dashboard.preferences',
      JSON.stringify({
        version: 1,
        preferences: {
          workflowListDisplayMode: 'sidebar',
        },
      }),
    );
    window.history.pushState({}, 'Workspace Test', '/workflows/new');
    const guardedTargets: string[] = [];
    const blockNavigation = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : undefined;
      guardedTargets.push(String(detail?.href || ''));
      event.preventDefault();
    };
    window.addEventListener('moonmind:workflow-start-route-change-request', blockNavigation);

    try {
      renderWorkspace({ page: 'dashboard', apiBase: '/api' });

      fireEvent.click(screen.getByRole('link', { name: 'Sidebar target workflow' }));

      expect(guardedTargets).toEqual(['/workflows/sidebar-target?source=temporal']);
      expect(window.location.pathname).toBe('/workflows/new');
    } finally {
      window.removeEventListener('moonmind:workflow-start-route-change-request', blockNavigation);
    }
  });
});
