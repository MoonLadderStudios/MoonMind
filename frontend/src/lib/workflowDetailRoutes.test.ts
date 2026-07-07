import { describe, expect, it } from 'vitest';

import {
  WORKFLOW_DETAIL_SUBROUTES,
  decodeWorkflowIdFromPath,
  isWorkflowDetailPath,
  workflowDetailSubrouteFromPath,
  workflowDetailSubrouteHref,
} from './workflowDetailRoutes';

describe('workflow detail route helpers', () => {
  it.each([
    ['/workflows/mm%3A123', 'mm:123', 'chat'],
    ['/workflows/mm%3A123/chat', 'mm:123', 'chat'],
    ['/workflows/mm%3A123/overview', 'mm:123', 'overview'],
    ['/workflows/mm%3A123/steps', 'mm:123', 'steps'],
    ['/workflows/mm%3A123/artifacts', 'mm:123', 'artifacts'],
    ['/workflows/mm%3A123/runs', 'mm:123', 'runs'],
    ['/workflows/mm%3A123/debug', 'mm:123', 'debug'],
  ] as const)('decodes supported route %s', (path, workflowId, subroute) => {
    expect(decodeWorkflowIdFromPath(path)).toBe(workflowId);
    expect(workflowDetailSubrouteFromPath(path)).toBe(subroute);
    expect(isWorkflowDetailPath(path)).toBe(true);
  });

  it('rejects unknown workflow detail subroutes and encoded slashes', () => {
    expect(isWorkflowDetailPath('/workflows/mm%3A123/files')).toBe(false);
    expect(decodeWorkflowIdFromPath('/workflows/mm%2Fbad/steps')).toBeNull();
  });

  it('builds hrefs for every supported subroute', () => {
    const search = new URLSearchParams('source=temporal&stateIn=failed');
    expect(WORKFLOW_DETAIL_SUBROUTES.map((subroute) => workflowDetailSubrouteHref('mm:123', subroute, search))).toEqual([
      '/workflows/mm%3A123?source=temporal&stateIn=failed',
      '/workflows/mm%3A123/overview?source=temporal&stateIn=failed',
      '/workflows/mm%3A123/steps?source=temporal&stateIn=failed',
      '/workflows/mm%3A123/artifacts?source=temporal&stateIn=failed',
      '/workflows/mm%3A123/runs?source=temporal&stateIn=failed',
      '/workflows/mm%3A123/debug?source=temporal&stateIn=failed',
    ]);
  });
});
