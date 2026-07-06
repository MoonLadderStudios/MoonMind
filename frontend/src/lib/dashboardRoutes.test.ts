import { describe, expect, it } from 'vitest';

import { resolveDashboardRoute } from './dashboardRoutes';

describe('dashboard route resolution', () => {
  it('resolves percent-encoded workflow detail IDs', () => {
    const path = '/workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95';

    expect(resolveDashboardRoute(path)).toEqual({
      page: 'workflows-workspace',
      dataWidePanel: true,
      currentPath: path,
    });
  });

  it('resolves encoded workflow IDs with detail tabs', () => {
    const path = '/workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95/steps';

    expect(resolveDashboardRoute(path)?.page).toBe('workflows-workspace');
  });

  it('resolves reserved-looking workflow IDs as detail pages', () => {
    for (const path of [
      '/workflows/settings',
      '/workflows/schedules',
      '/workflows/workers',
      '/workflows/settings/steps',
    ]) {
      expect(resolveDashboardRoute(path)?.page).toBe('workflows-workspace');
    }
  });

  it('keeps the new workflow route on the start page', () => {
    expect(resolveDashboardRoute('/workflows/new')).toEqual({
      page: 'workflow-start',
      dataWidePanel: true,
      currentPath: '/workflows/new',
    });
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
