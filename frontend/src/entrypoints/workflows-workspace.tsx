import { useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import WorkflowListPage from './workflow-list';
import WorkflowDetailEntrypoint, { WorkflowWorkspaceShell } from './workflow-detail';

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
  const isDesktop = useIsDesktop();
  const cfg = readWorkflowsWorkspaceDashboardConfig(payload);
  const temporalDashboard = cfg?.features?.temporalDashboard;
  const workspaceShellEnabled = temporalDashboard?.workspaceShellEnabled !== false;
  const listEnabled = temporalDashboard?.listEnabled !== false;

  if (!workflowId) {
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
      <WorkflowWorkspaceShell payload={payload} workflowId={workflowId} search={search} />
    </section>
  );
}

export default WorkflowsWorkspacePage;
