import { describe, expect, it } from 'vitest';

import { resolveDashboardRoute } from './dashboardRoutes';

describe('dashboard route resolution', () => {
  it('resolves percent-encoded workflow detail IDs', () => {
    const path = '/workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95';

    expect(resolveDashboardRoute(path)).toEqual({
      page: 'workflow-detail',
      dataWidePanel: false,
      currentPath: path,
    });
  });

  it('resolves encoded workflow IDs with detail tabs', () => {
    const path = '/workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95/steps';

    expect(resolveDashboardRoute(path)?.page).toBe('workflow-detail');
  });

  it('rejects encoded slashes inside workflow IDs', () => {
    expect(resolveDashboardRoute('/workflows/mm%2Fbad')).toBeNull();
  });

  it('resolves encoded manifest and schedule detail IDs', () => {
    expect(resolveDashboardRoute('/manifests/default%3Aworkflow')?.page).toBe(
      'manifests',
    );
    expect(resolveDashboardRoute('/schedules/nightly%3Abuild')?.page).toBe(
      'schedules',
    );
  });
});
