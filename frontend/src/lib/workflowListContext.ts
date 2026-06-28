export const WORKFLOW_LIST_CONTEXT_RETURN_PARAM = 'returnFromWorkflowDetail';

const WORKFLOW_LIST_CONTEXT_ALLOWLIST = new Set([
  'source',
  'limit',
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
  if (options.markDetailReturn && params.toString()) {
    params.set(WORKFLOW_LIST_CONTEXT_RETURN_PARAM, '1');
  }
  const query = params.toString();
  return query ? `/workflows?${query}` : '/workflows';
}
