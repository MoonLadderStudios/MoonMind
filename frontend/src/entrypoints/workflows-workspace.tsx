import { useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { LoadingPlaceholder } from '../components/dashboard/LoadingPlaceholder';
import WorkflowListPage from './workflow-list';
import WorkflowDetailEntrypoint, { WorkflowWorkspaceShell } from './workflow-detail';
import WorkflowStartPage from './workflow-start';
import {
  readWorkflowListDisplayMode,
} from '../lib/collectionListDisplayMode';
import { decodeWorkflowIdFromPath } from '../lib/workflowDetailRoutes';

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
        <WorkflowStartPage payload={payload} />
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
    return <WorkflowDetailEntrypoint payload={payload} />;
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
      />
    </section>
  );
}

export default WorkflowsWorkspacePage;
