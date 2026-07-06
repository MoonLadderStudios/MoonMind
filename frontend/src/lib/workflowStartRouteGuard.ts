export const WORKFLOW_START_ROUTE_CHANGE_REQUEST_EVENT =
  'moonmind:workflow-start-route-change-request';

export type WorkflowStartRouteChangeRequestDetail = {
  href: string;
};

export function requestWorkflowStartRouteChange(href: string): boolean {
  if (typeof window === 'undefined') {
    return true;
  }
  return window.dispatchEvent(
    new CustomEvent<WorkflowStartRouteChangeRequestDetail>(
      WORKFLOW_START_ROUTE_CHANGE_REQUEST_EVENT,
      {
        cancelable: true,
        detail: { href },
      },
    ),
  );
}
