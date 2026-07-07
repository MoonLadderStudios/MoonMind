export const WORKFLOW_DETAIL_SUBROUTES = [
  'chat',
  'overview',
  'steps',
  'artifacts',
  'runs',
  'debug',
] as const;

export type WorkflowDetailSubroute = (typeof WORKFLOW_DETAIL_SUBROUTES)[number];

const WORKFLOW_DETAIL_SUBROUTE_PATTERN = WORKFLOW_DETAIL_SUBROUTES.join('|');
const WORKFLOW_DETAIL_PATH_PATTERN = new RegExp(
  `^/workflows/([^/]+)(?:/(${WORKFLOW_DETAIL_SUBROUTE_PATTERN}))?/?$`,
);

export function decodeWorkflowIdFromPath(pathname: string): string | null {
  const match = pathname.match(WORKFLOW_DETAIL_PATH_PATTERN);
  if (!match?.[1] || match[1] === 'new') {
    return null;
  }
  try {
    const decoded = decodeURIComponent(match[1]);
    return decoded.includes('/') ? null : decoded;
  } catch {
    return null;
  }
}

export function workflowDetailSubrouteFromPath(pathname: string): WorkflowDetailSubroute {
  const match = pathname.match(WORKFLOW_DETAIL_PATH_PATTERN);
  return (match?.[2] as WorkflowDetailSubroute | undefined) || 'chat';
}

export function workflowDetailSubrouteHref(
  workflowId: string,
  subroute: WorkflowDetailSubroute,
  search: URLSearchParams,
): string {
  const suffix = subroute === 'chat' ? '' : `/${subroute}`;
  const query = search.toString();
  return `/workflows/${encodeURIComponent(workflowId)}${suffix}${query ? `?${query}` : ''}`;
}

export function isWorkflowDetailPath(pathname: string): boolean {
  return decodeWorkflowIdFromPath(pathname) !== null;
}
