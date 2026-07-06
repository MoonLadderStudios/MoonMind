import { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { workflowDetailHref } from '../lib/workflowListContext';
import {
  buildWorkflowListQueryParams,
  workflowListQueryString,
} from '../lib/workflowListQuery';
import {
  readDashboardPreferences,
  updateDashboardPreferences,
  type WorkflowListDisplayMode,
} from '../utils/dashboardPreferences';
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

function workflowWorkspaceRowId(row: unknown): string {
  if (!row || typeof row !== 'object') {
    return '';
  }
  const record = row as { workflowId?: unknown; taskId?: unknown };
  const id = typeof record.workflowId === 'string' ? record.workflowId : record.taskId;
  return typeof id === 'string' ? id.trim() : '';
}

function WorkflowListDisplayModeControl({
  mode,
  resolving,
  onSelect,
}: {
  mode: WorkflowListDisplayMode;
  resolving: boolean;
  onSelect: (mode: WorkflowListDisplayMode) => void;
}) {
  return (
    <fieldset className="workflow-list-display-mode-control" aria-label="Workflow list display">
      <legend className="sr-only">Workflow list display</legend>
      {[
        ['hidden', 'No list'],
        ['sidebar', 'Sidebar list'],
        ['table', 'Full screen table'],
      ].map(([value, label]) => (
        <label key={value} className="workflow-list-display-mode-option">
          <input
            type="radio"
            name="workflow-list-display-mode"
            value={value}
            checked={mode === value}
            disabled={resolving}
            onChange={() => onSelect(value as WorkflowListDisplayMode)}
          />
          <span>{label}</span>
        </label>
      ))}
    </fieldset>
  );
}

export function WorkflowsWorkspacePage({ payload }: { payload: BootPayload }) {
  const location = useLocation();
  const navigate = useNavigate();
  const workflowId = decodeWorkflowIdFromPath(location.pathname);
  const search = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const isDesktop = useIsDesktop();
  const cfg = readWorkflowsWorkspaceDashboardConfig(payload);
  const temporalDashboard = cfg?.features?.temporalDashboard;
  const workspaceShellEnabled = temporalDashboard?.workspaceShellEnabled !== false;
  const listEnabled = temporalDashboard?.listEnabled !== false;
  const [tableModeStatus, setTableModeStatus] = useState<string | null>(null);
  const [resolvingTableMode, setResolvingTableMode] = useState(false);

  const resolveTableModeSelection = async (mode: WorkflowListDisplayMode) => {
    updateDashboardPreferences({ workflowListDisplayMode: mode });
    setTableModeStatus(null);
    if (mode === 'table') {
      return;
    }

    setResolvingTableMode(true);
    try {
      const listQueryParams = buildWorkflowListQueryParams(new URLSearchParams(window.location.search));
      const listQuery = workflowListQueryString(listQueryParams);
      const response = await fetch(`${payload.apiBase}/executions?${listQuery}`);
      if (!response.ok) {
        setTableModeStatus('Workflow navigation is unavailable.');
        return;
      }
      const data = (await response.json()) as { items?: unknown[] } | null;
      const rows = data && Array.isArray(data.items) ? data.items : [];
      const ids = rows.map(workflowWorkspaceRowId).filter(Boolean);
      const lastSelectedWorkflowId = readDashboardPreferences().lastSelectedWorkflowId;
      const selectedId =
        lastSelectedWorkflowId && ids.includes(lastSelectedWorkflowId)
          ? lastSelectedWorkflowId
          : ids[0];

      if (!selectedId) {
        setTableModeStatus('No workflow is available to open from this list.');
        return;
      }

      navigate(workflowDetailHref(selectedId, listQueryParams));
    } catch {
      setTableModeStatus('Workflow navigation is unavailable.');
    } finally {
      setResolvingTableMode(false);
    }
  };

  if (!workflowId) {
    return (
      <section className="workflows-workspace-page" aria-label="Workflows workspace" data-jira-issue="MM-1061">
        {isDesktop ? (
          <WorkflowListDisplayModeControl
            mode="table"
            resolving={resolvingTableMode}
            onSelect={(mode) => {
              void resolveTableModeSelection(mode);
            }}
          />
        ) : null}
        {isDesktop && tableModeStatus ? (
          <p className="workflow-list-display-mode-status" role="status">
            {tableModeStatus}
          </p>
        ) : null}
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
