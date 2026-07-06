import { describe, expect, it } from 'vitest';

import {
  buildWorkflowListQueryKey,
  buildWorkflowListQueryParams,
  workflowListQueryString,
} from './workflowListQuery';

describe('workflowListQuery', () => {
  it('normalizes equivalent table and sidebar list contexts to the same query identity', () => {
    const tableParams = buildWorkflowListQueryParams(
      new URLSearchParams('source=temporal&limit=25&stateIn=completed&repoContains=moon%2Frepo'),
    );
    const sidebarParams = buildWorkflowListQueryParams(
      new URLSearchParams('repoContains=moon%2Frepo&stateIn=completed&pageSize=25&source=temporal'),
    );

    expect(workflowListQueryString(tableParams)).toBe(
      'source=temporal&repoContains=moon%2Frepo&stateIn=completed&pageSize=25',
    );
    expect(workflowListQueryString(sidebarParams)).toBe(workflowListQueryString(tableParams));
    expect(buildWorkflowListQueryKey(sidebarParams)).toEqual(
      buildWorkflowListQueryKey(tableParams),
    );
  });

  it('keeps non-matching list contexts on separate cache identities', () => {
    const currentPage = buildWorkflowListQueryParams(
      new URLSearchParams('source=temporal&pageSize=25&stateIn=completed&nextPageToken=page-2'),
    );
    const firstPage = buildWorkflowListQueryParams(
      new URLSearchParams('source=temporal&pageSize=25&stateIn=completed'),
    );

    expect(buildWorkflowListQueryKey(currentPage)).not.toEqual(
      buildWorkflowListQueryKey(firstPage),
    );
  });

  it('drops unsafe payload and mode parameters before generating query identity', () => {
    const params = buildWorkflowListQueryParams(
      new URLSearchParams(
        'source=temporal&workflowListDisplayMode=hidden&rawPrompt=secret&draft=full&token=abc&stateIn=executing',
      ),
    );

    expect(workflowListQueryString(params)).toBe('source=temporal&stateIn=executing&pageSize=25');
  });
});
