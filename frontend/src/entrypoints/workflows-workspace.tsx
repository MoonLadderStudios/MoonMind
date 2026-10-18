import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { LoadingPlaceholder } from '../components/dashboard/LoadingPlaceholder';
import { WorkflowWorkspaceShell } from '../components/workflows/WorkflowWorkspaceShell';
import WorkflowListPage from './workflow-list';
import {
  readWorkflowListDisplayMode,
} from '../lib/collectionListDisplayMode';
import { decodeWorkflowIdFromPath } from '../lib/workflowDetailRoutes';

// Create and Detail are separate chunks, fetched only when their route renders,
// so the list route does not download them.
const WorkflowDetailEntrypoint = lazy(() => import('./workflow-detail'));
const WorkflowDetailPage = lazy(async () => ({
  default: (await import('./workflow-detail')).WorkflowDetailPage,
}));
const WorkflowStartPage = lazy(() => import('./workflow-start'));

const DESKTOP_MEDIA_QUERY = '(min-width: 768px)';

function DetailLoading() {
  return <LoadingPlaceholder surface="workflow-detail" region="details" variant="detail" preserveContext />;
}

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

function isCreatePath(pathname: string): boolean {
  return pathname.replace(/\/$/, '') === '/workflows/new';
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

function readWorkflowListDisplayStatus(payload: BootPayload): string | null {
  const raw = payload.initialData as { workflowListDisplayStatus?: unknown } | undefined;
  return typeof raw?.workflowListDisplayStatus === 'string' ? raw.workflowListDisplayStatus : null;
}

export function WorkflowsWorkspacePage({ payload }: { payload: BootPayload }) {
  const location = useLocation();
  const workflowId = decodeWorkflowIdFromPath(location.pathname);
  const createRoute = isCreatePath(location.pathname);
  const search = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const isDesktop = useIsDesktop();
  const cfg = readWorkflowsWorkspaceDashboardConfig(payload);
  const temporalDashboard = cfg?.features?.temporalDashboard;
  const workspaceShellEnabled = temporalDashboard?.workspaceShellEnabled !== false;
  const listEnabled = temporalDashboard?.listEnabled !== false;
  const displayMode = readWorkflowListDisplayMode(payload);
  const displayStatus = readWorkflowListDisplayStatus(payload);

  if (createRoute) {
    return (
      <section
        className="workflows-workspace-page workflows-workspace-page--create"
        aria-label="Workflows workspace"
        data-jira-issue="MM-1115"
      >
        <Suspense fallback={<LoadingPlaceholder surface="workflow-start" region="form" variant="form-controls" />}>
          <WorkflowStartPage payload={payload} />
        </Suspense>
      </section>
    );
  }

  if (!workflowId) {
    if (displayStatus === 'Opening first workflow...') {
      return (
        <section
          className="workflows-workspace-page workflows-workspace-page--selected"
          aria-label="Workflows workspace"
          data-jira-issue="MM-1061"
        >
          <div
            className="workflow-workspace-shell"
            data-sidebar-collapsed="true"
            data-workflow-list-display-mode={displayMode ?? 'hidden'}
          >
            <main className="workflow-workspace-detail" aria-label="Workflow detail">
              <div className="workflow-workspace-opening-state" role="status">
                <LoadingPlaceholder
                  surface="workflow-detail"
                  region="details"
                  variant="detail"
                  preserveContext
                />
                <p>{displayStatus}</p>
              </div>
            </main>
          </div>
        </section>
      );
    }
    return (
      <section className="workflows-workspace-page" aria-label="Workflows workspace" data-jira-issue="MM-1061">
        <WorkflowListPage payload={payload} />
      </section>
    );
  }

  if (!isDesktop || !workspaceShellEnabled || !listEnabled) {
    return (
      <Suspense fallback={<DetailLoading />}>
        <WorkflowDetailEntrypoint payload={payload} />
      </Suspense>
    );
  }

  return (
    <section
      className="workflows-workspace-page workflows-workspace-page--selected"
      aria-label="Workflows workspace"
      data-jira-issue="MM-1061"
    >
      <WorkflowWorkspaceShell
        payload={payload}
        workflowId={workflowId}
        search={search}
        displayMode={displayMode}
      >
        <Suspense fallback={<DetailLoading />}>
          <WorkflowDetailPage payload={payload} />
        </Suspense>
      </WorkflowWorkspaceShell>
    </section>
  );
}

export default WorkflowsWorkspacePage;
