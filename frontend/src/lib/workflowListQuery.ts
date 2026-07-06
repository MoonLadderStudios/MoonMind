import { workflowListContextParams } from './workflowListContext';

export const DEFAULT_WORKFLOW_LIST_PAGE_SIZE = '25';

export type WorkflowListQueryKey = readonly ['workflow-list', string];

const PRIMARY_PARAM_ORDER = ['source', 'nextPageToken', 'pageSize'];

function orderedEntries(params: URLSearchParams): Array<[string, string]> {
  const entries = Array.from(params.entries());
  return entries.sort(([leftKey, leftValue], [rightKey, rightValue]) => {
    const leftPrimary = PRIMARY_PARAM_ORDER.indexOf(leftKey);
    const rightPrimary = PRIMARY_PARAM_ORDER.indexOf(rightKey);
    if (leftPrimary !== -1 || rightPrimary !== -1) {
      if (leftPrimary === -1) return 1;
      if (rightPrimary === -1) return -1;
      if (leftPrimary !== rightPrimary) return leftPrimary - rightPrimary;
    }
    if (leftKey !== rightKey) return leftKey.localeCompare(rightKey);
    return leftValue.localeCompare(rightValue);
  });
}

export function buildWorkflowListQueryParams(source: URLSearchParams): URLSearchParams {
  const safe = workflowListContextParams(source);
  const pageSize =
    safe.get('pageSize') || safe.get('limit') || DEFAULT_WORKFLOW_LIST_PAGE_SIZE;
  safe.delete('limit');
  safe.delete('pageSize');
  if (!safe.has('source')) {
    safe.set('source', 'temporal');
  }
  safe.set('pageSize', pageSize);

  const normalized = new URLSearchParams();
  for (const [key, value] of orderedEntries(safe)) {
    normalized.append(key, value);
  }
  return normalized;
}

export function workflowListQueryString(params: URLSearchParams): string {
  return orderedEntries(params)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&');
}

export function buildWorkflowListQueryKey(params: URLSearchParams): WorkflowListQueryKey {
  return ['workflow-list', workflowListQueryString(params)] as const;
}
