import { useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import {
  readDashboardPreferences,
  updateDashboardPreferences,
  type WorkflowListDisplayMode,
} from '../utils/dashboardPreferences';
import { requestWorkflowStartRouteChange } from '../lib/workflowStartRouteGuard';
import WorkflowListPage from './workflow-list';
import WorkflowDetailEntrypoint, {
  WorkflowWorkspaceShell,
  WorkflowWorkspaceSidebarLayout,
} from './workflow-detail';
import WorkflowStartPage from './workflow-start';

const DESKTOP_MEDIA_QUERY = '(min-width: 768px)';

function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return true;
    }
    return window.matchMedia(DESKTOP_MEDIA_QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }
    const query = window.matchMedia(DESKTOP_MEDIA_QUERY);
    const update = () => setIsDesktop(query.matches);
    update();
    query.addEventListener?.('change', update);
    return () => query.removeEventListener?.('change', update);
  }, []);

  return isDesktop;
}

function decodeWorkflowIdFromPath(pathname: string): string | null {
  const match = pathname.match(/^\/workflows\/([^/]+)(?:\/(?:steps|artifacts|runs|debug))?\/?$/);
  if (!match?.[1]) {
    return null;
  }
  try {
    const decoded = decodeURIComponent(match[1]);
    return decoded.includes('/') ? null : decoded;
  } catch {
    return null;
  }
}

function isCreatePath(pathname: string): boolean {
  return pathname.replace(/\/$/, '') === '/workflows/new';
}

function resolveWorkspaceDisplayMode(pathname: string): WorkflowListDisplayMode {
  if (pathname.replace(/\/$/, '') === '/workflows') {
    return 'table';
  }
  const persisted = readDashboardPreferences().workflowListDisplayMode;
  if (persisted === 'hidden' || persisted === 'sidebar') {
    return persisted;
  }
  return isCreatePath(pathname) ? 'hidden' : 'sidebar';
}

type WorkflowsWorkspaceDashboardConfig = {
  features?: {
    temporalDashboard?: {
      listEnabled?: boolean;
      workspaceShellEnabled?: boolean;
    };
  };
};

function readWorkflowsWorkspaceDashboardConfig(
  payload: BootPayload,
): WorkflowsWorkspaceDashboardConfig | undefined {
  const raw = payload.initialData as { dashboardConfig?: WorkflowsWorkspaceDashboardConfig } | undefined;
  return raw?.dashboardConfig;
}

export function WorkflowsWorkspacePage({ payload }: { payload: BootPayload }) {
  const location = useLocation();
  const workflowId = decodeWorkflowIdFromPath(location.pathname);
  const search = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const createRoute = isCreatePath(location.pathname);
  const [displayMode, setDisplayMode] = useState<WorkflowListDisplayMode>(() => (
    resolveWorkspaceDisplayMode(location.pathname)
  ));
  const isDesktop = useIsDesktop();
  const cfg = readWorkflowsWorkspaceDashboardConfig(payload);
  const temporalDashboard = cfg?.features?.temporalDashboard;
  const workspaceShellEnabled = temporalDashboard?.workspaceShellEnabled !== false;
  const listEnabled = temporalDashboard?.listEnabled !== false;

  useEffect(() => {
    setDisplayMode(resolveWorkspaceDisplayMode(location.pathname));
  }, [location.pathname]);

  useEffect(() => {
    const update = (event: Event) => {
      const nextMode = event instanceof CustomEvent ? event.detail : undefined;
      if (nextMode === 'hidden' || nextMode === 'sidebar' || nextMode === 'table') {
        updateDashboardPreferences({ workflowListDisplayMode: nextMode });
        setDisplayMode(nextMode);
      }
    };
    window.addEventListener('moonmind:workflow-list-display-mode', update);
    return () => window.removeEventListener('moonmind:workflow-list-display-mode', update);
  }, []);

  if (!workflowId && !createRoute) {
    return (
      <section className="workflows-workspace-page" aria-label="Workflows workspace" data-jira-issue="MM-1061 MM-1115">
        <WorkflowListPage payload={payload} />
      </section>
    );
  }

  if (createRoute) {
    if (!isDesktop || !workspaceShellEnabled || !listEnabled || displayMode === 'hidden') {
      return (
        <section
          className="workflows-workspace-page workflows-workspace-page--create"
          aria-label="Workflows workspace"
          data-jira-issue="MM-1115"
        >
          <WorkflowStartPage payload={payload} />
        </section>
      );
    }

    return (
      <section
        className="workflows-workspace-page workflows-workspace-page--create workflows-workspace-page--selected"
        aria-label="Workflows workspace"
        data-jira-issue="MM-1115"
      >
        <WorkflowWorkspaceSidebarLayout
          payload={payload}
          search={search}
          activeWorkflowId=""
          primaryAriaLabel="Create workflow"
          onSidebarClose={() => {
            updateDashboardPreferences({
              workflowWorkspaceSidebarCollapsed: true,
              workflowListDisplayMode: 'hidden',
            });
            setDisplayMode('hidden');
            window.dispatchEvent(new CustomEvent('moonmind:workflow-list-display-mode', { detail: 'hidden' }));
          }}
          onSidebarNavigate={requestWorkflowStartRouteChange}
        >
          <WorkflowStartPage payload={payload} />
        </WorkflowWorkspaceSidebarLayout>
      </section>
    );
  }

  if (!workflowId) {
    return <WorkflowListPage payload={payload} />;
  }

  if (!isDesktop || !workspaceShellEnabled || !listEnabled) {
    return <WorkflowDetailEntrypoint payload={payload} />;
  }

  return (
    <section
      className="workflows-workspace-page workflows-workspace-page--selected"
      aria-label="Workflows workspace"
      data-jira-issue="MM-1061 MM-1115"
    >
      <WorkflowWorkspaceShell
        payload={payload}
        workflowId={workflowId}
        search={search}
        displayMode={displayMode === 'hidden' ? 'hidden' : 'sidebar'}
      />
    </section>
  );
}

export default WorkflowsWorkspacePage;
