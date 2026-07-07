export const WORKFLOW_DETAIL_SUBROUTES = [
  'chat',
  'overview',
  'execution',
  'evidence',
  'debug',
] as const;

export type WorkflowDetailSubroute = (typeof WORKFLOW_DETAIL_SUBROUTES)[number];

export const WORKFLOW_DETAIL_SUBROUTE_ALIASES = {
  steps: 'execution',
  runs: 'execution',
  artifacts: 'evidence',
} as const satisfies Record<string, WorkflowDetailSubroute>;

export const WORKFLOW_DETAIL_SUPPORTED_SUBROUTES = [
  ...WORKFLOW_DETAIL_SUBROUTES,
  ...Object.keys(WORKFLOW_DETAIL_SUBROUTE_ALIASES),
] as const;

const WORKFLOW_DETAIL_SUBROUTE_PATTERN = WORKFLOW_DETAIL_SUPPORTED_SUBROUTES.join('|');
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
  const subroute = match?.[2] || '';
  return (
    WORKFLOW_DETAIL_SUBROUTE_ALIASES[
      subroute as keyof typeof WORKFLOW_DETAIL_SUBROUTE_ALIASES
    ] ||
    (subroute as WorkflowDetailSubroute | undefined) ||
    'chat'
  );
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
