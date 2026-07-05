export type WorkflowListDisplayMode = 'hidden' | 'sidebar' | 'table';

export const WORKFLOW_LIST_DISPLAY_MODE_CHANGE_EVENT = 'moonmind:workflow-list-display-mode-change';

export type WorkflowListDisplayModeChangeDetail = {
  mode: WorkflowListDisplayMode;
};

export function dispatchWorkflowListDisplayModeChange(mode: WorkflowListDisplayMode): void {
  window.dispatchEvent(
    new CustomEvent<WorkflowListDisplayModeChangeDetail>(
      WORKFLOW_LIST_DISPLAY_MODE_CHANGE_EVENT,
      { detail: { mode } },
    ),
  );
}
