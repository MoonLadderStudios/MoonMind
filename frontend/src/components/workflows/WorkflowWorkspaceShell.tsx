import type { ReactNode } from 'react';
import { skipToken, useQuery } from '@tanstack/react-query';

import type { BootPayload } from '../../boot/parseBootPayload';
import type { CollectionListDisplayMode } from '../../lib/collectionListDisplayMode';
import { workflowDetailQueryKey } from '../../lib/workflowDetailRoutes';
import { workflowWorkspaceRowFromDetail } from '../../lib/workflowWorkspaceList';
import { readDashboardPreferences } from '../../utils/dashboardPreferences';
import { CollectionWorkspace } from '../CollectionWorkspace';
import { WorkflowWorkspaceSidebarPanel } from './WorkflowWorkspaceSidebar';

type WorkflowDetailRowSource = Parameters<typeof workflowWorkspaceRowFromDetail>[0];

/**
 * Desktop workspace frame (sidebar list + detail). It lives outside the
 * workflow-detail module so the workspace can render the frame and sidebar
 * while the detail page (`children`) loads lazily.
 */
export function WorkflowWorkspaceShell({
  payload,
  workflowId,
  search,
  displayMode,
  children,
}: {
  payload: BootPayload;
  workflowId: string;
  search: URLSearchParams;
  displayMode?: CollectionListDisplayMode | undefined;
  children: ReactNode;
}) {
  const effectiveDisplayMode = displayMode ?? (
    readDashboardPreferences().workflowListDisplayMode
  );
  const sourceTemporal = search.get('source') === 'temporal';
  // The detail page fetches and polls this snapshot; the shell only observes
  // the shared cache entry to pin the selected row in the sidebar.
  const selectedWorkflowQuery = useQuery<WorkflowDetailRowSource>({
    queryKey: workflowDetailQueryKey(workflowId, sourceTemporal),
    queryFn: skipToken,
  });
  const pinnedCurrentRow = selectedWorkflowQuery.data
    ? workflowWorkspaceRowFromDetail(selectedWorkflowQuery.data)
    : null;

  return (
    <CollectionWorkspace
      collection="workflow"
      mode={effectiveDisplayMode === 'sidebar' ? 'sidebar' : 'detail'}
      sidebar={effectiveDisplayMode === 'sidebar' ? (
        <WorkflowWorkspaceSidebarPanel
          payload={payload}
          activeWorkflowId={workflowId}
          pinnedCurrentRow={pinnedCurrentRow}
          search={search}
        />
      ) : null}
      className="workflow-workspace-shell"
      primaryClassName="workflow-workspace-detail"
      primaryLabel="Workflow detail"
      data-sidebar-collapsed={effectiveDisplayMode === 'sidebar' ? 'false' : 'true'}
      data-workflow-list-display-mode={effectiveDisplayMode}
      data-jira-issue="MM-997 MM-999 MM-1000 MM-1002 MM-1005 MM-1008 MM-1138"
      data-source-issue="MM-975"
    >
      {children}
    </CollectionWorkspace>
  );
}
