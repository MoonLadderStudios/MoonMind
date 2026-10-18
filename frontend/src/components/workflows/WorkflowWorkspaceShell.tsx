import { useCallback, useSyncExternalStore, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import type { BootPayload } from '../../boot/parseBootPayload';
import type { CollectionListDisplayMode } from '../../lib/collectionListDisplayMode';
import { workflowDetailQueryKey } from '../../lib/workflowDetailRoutes';
import { workflowWorkspaceRowFromDetail } from '../../lib/workflowWorkspaceList';
import { readDashboardPreferences } from '../../utils/dashboardPreferences';
import { CollectionWorkspace } from '../CollectionWorkspace';
import { WorkflowWorkspaceSidebarPanel } from './WorkflowWorkspaceSidebar';

type WorkflowDetailRowSource = Parameters<typeof workflowWorkspaceRowFromDetail>[0];

/**
 * Reads the detail snapshot that the (lazily loaded) detail page fetches and
 * polls. A query observer here would replace that query's fetch options with
 * the shell's, so later refetches (such as the invalidation after an action)
 * would have nothing to run; a cache subscription only reads.
 */
function useCachedWorkflowDetail(
  workflowId: string,
  sourceTemporal: boolean,
): WorkflowDetailRowSource | undefined {
  const queryClient = useQueryClient();
  const queryKey = workflowDetailQueryKey(workflowId, sourceTemporal);
  const subscribe = useCallback(
    (onChange: () => void) => queryClient.getQueryCache().subscribe(onChange),
    [queryClient],
  );
  return useSyncExternalStore(subscribe, () =>
    queryClient.getQueryData<WorkflowDetailRowSource>(queryKey),
  );
}

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
  const selectedWorkflow = useCachedWorkflowDetail(workflowId, sourceTemporal);
  const pinnedCurrentRow = selectedWorkflow
    ? workflowWorkspaceRowFromDetail(selectedWorkflow)
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
