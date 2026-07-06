export const WORKFLOW_LIST_CONTEXT_RETURN_PARAM = 'returnFromWorkflowDetail';
export const WORKFLOW_LIST_RETURN_FOCUS_INTENT_KEY = 'moonmind.workflowList.returnFocusIntent';

export function markWorkflowListReturnFocusIntent(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(WORKFLOW_LIST_RETURN_FOCUS_INTENT_KEY, '1');
  } catch {
    // Ignore storage failures; query-marked returns still focus the list.
  }
}

export function consumeWorkflowListReturnFocusIntent(source: URLSearchParams): boolean {
  const hasReturnParam = source.get(WORKFLOW_LIST_CONTEXT_RETURN_PARAM) === '1';
  if (typeof window === 'undefined') return hasReturnParam;
  try {
    const hasStoredIntent = window.sessionStorage.getItem(WORKFLOW_LIST_RETURN_FOCUS_INTENT_KEY) === '1';
    window.sessionStorage.removeItem(WORKFLOW_LIST_RETURN_FOCUS_INTENT_KEY);
    return hasReturnParam || hasStoredIntent;
  } catch {
    return hasReturnParam;
  }
}

const WORKFLOW_LIST_CONTEXT_ALLOWLIST = new Set([
  'source',
  'limit',
  'pageSize',
  'nextPageToken',
  'workflowIdContains',
  'workflowId',
  'stateIn',
  'stateNotIn',
  'state',
  'repoIn',
  'repoNotIn',
  'repoBlank',
  'repoContains',
  'repoExact',
  'repo',
  'integration',
  'targetRuntimeIn',
  'targetRuntimeNotIn',
  'targetRuntimeBlank',
  'targetRuntime',
  'targetSkillIn',
  'targetSkillNotIn',
  'targetSkillBlank',
  'titleContains',
  'scheduledFrom',
  'scheduledTo',
  'scheduledBlank',
  'updatedFrom',
  'updatedTo',
  'createdFrom',
  'createdTo',
  'finishedFrom',
  'finishedTo',
  'finishedBlank',
]);

export function workflowListContextParams(source: URLSearchParams): URLSearchParams {
  const params = new URLSearchParams();
  source.forEach((value, key) => {
    if (WORKFLOW_LIST_CONTEXT_ALLOWLIST.has(key)) {
      params.append(key, value);
    }
  });
  return params;
}

export function workflowDetailHref(workflowId: string, source: URLSearchParams): string {
  const params = workflowListContextParams(source);
  if (!params.has('source')) {
    params.set('source', 'temporal');
  }
  const query = params.toString();
  return `/workflows/${encodeURIComponent(workflowId)}${query ? `?${query}` : ''}`;
}

export function workflowListHrefFromContext(
  source: URLSearchParams,
  options: { markDetailReturn?: boolean } = {},
): string {
  const params = workflowListContextParams(source);
  params.delete('source');
  if (options.markDetailReturn) {
    params.delete('nextPageToken');
  }
  if (options.markDetailReturn && params.toString()) {
    params.set(WORKFLOW_LIST_CONTEXT_RETURN_PARAM, '1');
  }
  const query = params.toString();
  return query ? `/workflows?${query}` : '/workflows';
}
