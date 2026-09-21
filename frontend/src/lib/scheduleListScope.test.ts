import { describe, expect, it } from 'vitest';

import { stripLegacyAccountScopeParam } from '../entrypoints/schedules';

// MoonLadderStudios/MoonMind#4353 (WRK-10): server-resource queries must not
// be partitioned by user identity. Stale account-era boot payloads may still
// carry `scope`, so the schedule list endpoint drops it at the use site
// instead of reusing the stale partitioning.
describe('stripLegacyAccountScopeParam', () => {
  it('removes the account scope partition while preserving real query keys', () => {
    expect(stripLegacyAccountScopeParam('/api/recurring-workflows?scope=personal'))
      .toBe('/api/recurring-workflows');
    expect(
      stripLegacyAccountScopeParam('/api/recurring-workflows?scope=personal&limit=50&sort=updatedAt'),
    ).toBe('/api/recurring-workflows?limit=50&sort=updatedAt');
  });

  it('leaves scope-free endpoints untouched', () => {
    expect(stripLegacyAccountScopeParam('/api/recurring-workflows')).toBe('/api/recurring-workflows');
    expect(stripLegacyAccountScopeParam('/api/recurring-workflows?limit=50'))
      .toBe('/api/recurring-workflows?limit=50');
  });
});
