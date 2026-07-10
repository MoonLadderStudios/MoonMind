import { describe, expect, it } from 'vitest';

import { resolveDashboardRoute } from './dashboardRoutes';

describe('dashboard route resolution', () => {
  it.each(['/omnigent/agents', '/omnigent/policies'])(
    'resolves the %s inventory route independently',
    (path) => {
      expect(resolveDashboardRoute(path)).toEqual({
        page: 'omnigent-inventory',
        dataWidePanel: true,
        currentPath: path,
      });
    },
  );
  it.each(['/artifacts', '/observability'])('resolves the %s evidence collection route', (path) => {
    expect(resolveDashboardRoute(path)).toEqual({
      page: 'artifacts',
      dataWidePanel: true,
      currentPath: path,
    });
  });
  it('resolves percent-encoded workflow detail IDs', () => {
    const path = '/workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95';

    expect(resolveDashboardRoute(path)).toEqual({
      page: 'workflows-workspace',
      dataWidePanel: true,
      currentPath: path,
    });
  });

  it.each(['chat', 'overview', 'execution', 'evidence', 'steps', 'artifacts', 'runs', 'debug'])(
    'resolves encoded workflow IDs with the %s detail tab',
    (tab) => {
      const path = `/workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95/${tab}`;

      expect(resolveDashboardRoute(path)?.page).toBe('workflows-workspace');
    },
  );

  it('rejects unknown workflow detail tabs', () => {
    expect(resolveDashboardRoute('/workflows/mm%3A123/files')).toBeNull();
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

  it('keeps the new workflow route inside the workspace compositor', () => {
    expect(resolveDashboardRoute('/workflows/new')).toEqual({
      page: 'workflows-workspace',
      dataWidePanel: true,
      currentPath: '/workflows/new',
    });
  });

  it('rejects encoded slashes inside workflow IDs', () => {
    expect(resolveDashboardRoute('/workflows/mm%2Fbad')).toBeNull();
  });

  it('resolves encoded manifest and schedule detail IDs', () => {
    expect(resolveDashboardRoute('/manifests/default%3Aworkflow')).toEqual({
      page: 'manifests',
      dataWidePanel: true,
      currentPath: '/manifests/default%3Aworkflow',
    });
    expect(resolveDashboardRoute('/schedules/nightly%3Abuild')).toEqual({
      page: 'schedules',
      dataWidePanel: true,
      currentPath: '/schedules/nightly%3Abuild',
    });
  });

  it.each(['/schedules', '/manifests'])('uses the fluid shell for the %s collection', (path) => {
    expect(resolveDashboardRoute(path)?.dataWidePanel).toBe(true);
  });

  it('resolves the remediation collection as a data-wide route', () => {
    expect(resolveDashboardRoute('/remediations')).toEqual({
      page: 'remediations',
      dataWidePanel: true,
      currentPath: '/remediations',
    });
  });
});
