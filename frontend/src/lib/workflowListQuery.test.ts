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
      'source=temporal&pageSize=25&stateIn=completed&repoContains=moon%2Frepo',
    );
    expect(workflowListQueryString(sidebarParams)).toBe(workflowListQueryString(tableParams));
    expect(buildWorkflowListQueryKey(sidebarParams)).toEqual(
      buildWorkflowListQueryKey(tableParams),
    );
  });

  it('distinguishes a comma-containing exact profile ID from legacy CSV membership', () => {
    const exact = buildWorkflowListQueryParams(new URLSearchParams('providerProfileIdIn=account%2Cprimary&providerProfileIdIn=second&providerProfileIdNotIn=third%2Cone'));
    const legacy = buildWorkflowListQueryParams(new URLSearchParams('providerProfileIn=account%2Cprimary'));
    expect(exact.getAll('providerProfileIdIn')).toEqual(['account,primary', 'second']);
    expect(exact.getAll('providerProfileIdNotIn')).toEqual(['third,one']);
    expect(buildWorkflowListQueryKey(exact)).not.toEqual(buildWorkflowListQueryKey(legacy));
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

    expect(workflowListQueryString(params)).toBe('source=temporal&pageSize=25&stateIn=executing');
  });

  it('MoonLadderStudios/MoonMind#4640 keeps Provider Profile IDs, states, and legacy runtime distinct', () => {
    const params = buildWorkflowListQueryParams(
      new URLSearchParams(
        'targetRuntimeIn=codex_cli&providerProfileStateIn=pending&providerProfileIn=acct-1%2Cacct-2&source=temporal',
      ),
    );

    expect(workflowListQueryString(params)).toBe(
      'source=temporal&pageSize=25&providerProfileIn=acct-1%2Cacct-2&providerProfileStateIn=pending&targetRuntimeIn=codex_cli',
    );
    const profileOnly = buildWorkflowListQueryParams(
      new URLSearchParams('source=temporal&providerProfileIn=acct-1'),
    );
    const runtimeOnly = buildWorkflowListQueryParams(
      new URLSearchParams('source=temporal&targetRuntimeIn=acct-1'),
    );
    expect(buildWorkflowListQueryKey(profileOnly)).not.toEqual(buildWorkflowListQueryKey(runtimeOnly));
  });
});
