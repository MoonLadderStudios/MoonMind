import { describe, expect, it } from 'vitest';

import {
  DASHBOARD_DESTINATION_GROUPS,
  DASHBOARD_DESTINATIONS,
  DASHBOARD_REACT_ROUTE_PATHS,
  destinationState,
  destinationForPath,
  isDashboardInternalUrl,
  legacySettingsRedirect,
  matchesDashboardDestinationRegistry,
  payloadForDashboardRoute,
  resolveDashboardRoute,
  visiblePrimaryDestinations,
  visibleSystemDestinations,
} from './dashboardRoutes';

describe('dashboard route resolution', () => {
  it('keeps one canonical typed inventory for every major destination', () => {
    expect(DASHBOARD_DESTINATIONS.map(({ key }) => key)).toEqual([
      'workflows', 'create', 'recurring', 'skills', 'manifests',
      'omnigent-agents', 'omnigent-policies', 'remediation', 'artifacts',
      'settings-providers-secrets', 'settings-user-workspace', 'settings-operations',
    ]);
    expect(new Set(DASHBOARD_DESTINATIONS.map(({ canonicalPath }) => canonicalPath)).size).toBe(12);
    expect(DASHBOARD_REACT_ROUTE_PATHS).toEqual(
      Array.from(new Set([
        ...DASHBOARD_DESTINATIONS.flatMap(({ pathPatterns }) => pathPatterns),
        '/settings',
        '/settings/*',
      ])),
    );
    expect(DASHBOARD_DESTINATIONS.find(({ key }) => key === 'skills')?.displayMode).toBe('skills-list');
    expect(DASHBOARD_DESTINATION_GROUPS).toContainEqual({
      key: 'configuration',
      label: 'Configuration',
      triggerLabel: 'Settings',
      triggerIconKey: 'settings',
      destinationKeys: [
        'settings-providers-secrets',
        'settings-user-workspace',
        'settings-operations',
      ],
    });
  });

  it('derives shown, hidden, and unavailable states from capability data', () => {
    const skills = DASHBOARD_DESTINATIONS.find(({ key }) => key === 'skills')!;
    expect(destinationState(skills, { features: { skills: true } })).toBe('shown');
    expect(destinationState(skills, { features: {} })).toBe('hidden');
    expect(destinationState(skills, { features: { skills: false } })).toBe('unavailable');
  });

  it('preserves registry order while grouping enabled navigation destinations', () => {
    const features = Object.fromEntries(DASHBOARD_DESTINATIONS.map(({ capabilityKey }) => [capabilityKey, true]));
    features.omnigentPolicies = false;
    const info = { features };
    expect(visiblePrimaryDestinations(info).map(({ key }) => key)).toEqual([
      'workflows', 'create',
    ]);
    expect(visibleSystemDestinations(info).map(({ key }) => key)).toEqual([
      'recurring', 'skills', 'manifests', 'omnigent-agents', 'remediation', 'artifacts',
      'settings-providers-secrets', 'settings-user-workspace', 'settings-operations',
    ]);
  });

  it('keeps baseline primary navigation visible while UI capabilities are unavailable', () => {
    expect(visiblePrimaryDestinations(null).map(({ key }) => key)).toEqual([
      'workflows', 'create',
    ]);
    expect(visibleSystemDestinations(null)).toEqual([]);
  });

  it.each([
    ['/manifests/default', 'manifests'],
    ['/artifacts/run/1', 'artifacts'],
    ['/observability/run/1', 'artifacts'],
    ['/remediations/mm:1', 'remediation'],
    ['/settings/providers-secrets', 'settings-providers-secrets'],
    ['/settings/user-workspace', 'settings-user-workspace'],
    ['/settings/operations', 'settings-operations'],
    ['/schedules', 'recurring'],
    ['/schedules/nightly%3Abuild', 'recurring'],
    ['/skills', 'skills'],
    ['/skills/speckit-orchestrate', 'skills'],
  ])('resolves %s to the active System destination', (path, key) => {
    const destination = destinationForPath(path);
    expect(destination?.key).toBe(key);
    expect(destination?.navigationGroup).not.toBe('primary');
  });

  it('detects backend destination inventory drift', () => {
    const serverInventory = DASHBOARD_DESTINATIONS.map(({ page: _page, dataWidePanel: _wide, ...item }) => ({ ...item }));
    expect(matchesDashboardDestinationRegistry(serverInventory)).toBe(true);
    expect(matchesDashboardDestinationRegistry(serverInventory.slice(1))).toBe(false);
    expect(matchesDashboardDestinationRegistry(serverInventory.map((item, index) => (
      index === 0 ? { ...item, capabilityKey: 'drifted' } : item
    )))).toBe(false);
  });

  it.each(['/artifacts/report/123', '/observability/runs/today', '/remediations/mm%3A123', '/omnigent/agents/coding']) (
    'resolves the extensionless collection deep link %s',
    (path) => expect(resolveDashboardRoute(path)).not.toBeNull(),
  );
  it('keeps extensionless unknown Settings aliases outside the route-owned page registry', () => {
    expect(DASHBOARD_REACT_ROUTE_PATHS).toContain('/settings/*');
    expect(resolveDashboardRoute('/settings/provider-profiles')).toBeNull();
  });
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
  it.each([
    ['/settings', 'settings-entry'],
    ['/settings/providers-secrets', 'settings-providers-secrets'],
    ['/settings/user-workspace', 'settings-user-workspace'],
    ['/settings/operations', 'settings-operations'],
  ])('resolves the canonical Settings route %s to %s', (path, page) => {
    expect(resolveDashboardRoute(path)).toEqual({
      page,
      dataWidePanel: true,
      currentPath: path,
    });
  });
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

  it.each(['/schedules', '/manifests', '/skills'])('uses the fluid shell for the %s collection', (path) => {
    expect(resolveDashboardRoute(path)?.dataWidePanel).toBe(true);
  });

  it('resolves skill detail routes into the fluid skills page', () => {
    expect(resolveDashboardRoute('/skills/speckit-orchestrate')).toEqual({
      page: 'skills',
      dataWidePanel: true,
      currentPath: '/skills/speckit-orchestrate',
    });
  });

  it('resolves the remediation collection as a data-wide route', () => {
    expect(resolveDashboardRoute('/remediations')).toEqual({
      page: 'remediations',
      dataWidePanel: true,
      currentPath: '/remediations',
    });
  });

  it('attaches the remediation capability and compact endpoint contract to route payloads', () => {
    const route = resolveDashboardRoute('/remediations');

    expect(route).not.toBeNull();
    expect(payloadForDashboardRoute(
      { page: 'dashboard', apiBase: '/api' },
      route!,
      {
        features: { remediationCollection: true },
        endpoints: { remediations: '/api/executions/remediations' },
      },
    )).toMatchObject({
      page: 'remediations',
      features: { remediationCollection: true },
      initialData: {
        dashboardConfig: { initialPath: '/remediations' },
        layout: { dataWidePanel: true },
        uiEndpoints: { remediations: '/api/executions/remediations' },
      },
    });
  });

  it('redirects legacy /secrets and /workers preserving safe filters and dropping section', () => {
    expect(legacySettingsRedirect('/secrets', '')).toBe('/settings/providers-secrets');
    expect(legacySettingsRedirect('/workers', '')).toBe('/settings/operations');
    expect(legacySettingsRedirect('/secrets', '?runtime=codex')).toBe('/settings/providers-secrets?runtime=codex');
    expect(legacySettingsRedirect('/workers', '?status=paused')).toBe('/settings/operations?status=paused');
    expect(legacySettingsRedirect('/secrets', '?section=providers&runtime=codex')).toBe('/settings/providers-secrets?runtime=codex');
    expect(legacySettingsRedirect('/secrets', '?runtime=codex&q=search&section=x')).toBe('/settings/providers-secrets?runtime=codex');
    expect(legacySettingsRedirect('/workers', '?q=search&status=paused&section=x')).toBe('/settings/operations?status=paused');
    expect(legacySettingsRedirect('/secrets', '?unknown=1')).toBe('/settings/providers-secrets');
  });

  it('treats legacy redirects as internal URLs and preserves safe query strings', () => {
    const origin = window.location.origin;
    expect(isDashboardInternalUrl(new URL(`${origin}/secrets?runtime=codex`))).toBe(true);
    expect(isDashboardInternalUrl(new URL(`${origin}/workers?status=paused`))).toBe(true);
    expect(isDashboardInternalUrl(new URL(`${origin}/settings/providers-secrets`))).toBe(true);
    expect(isDashboardInternalUrl(new URL(`${origin}/unknown`))).toBe(false);
    expect(isDashboardInternalUrl(new URL('https://example.com/secrets'))).toBe(false);
  });

  it('exposes canonical Settings paths with correct destination metadata', () => {
    const providers = DASHBOARD_DESTINATIONS.find(({ key }) => key === 'settings-providers-secrets')!;
    const workspace = DASHBOARD_DESTINATIONS.find(({ key }) => key === 'settings-user-workspace')!;
    const ops = DASHBOARD_DESTINATIONS.find(({ key }) => key === 'settings-operations')!;
    expect(providers.canonicalPath).toBe('/settings/providers-secrets');
    expect(workspace.canonicalPath).toBe('/settings/user-workspace');
    expect(ops.canonicalPath).toBe('/settings/operations');
    for (const dest of [providers, workspace, ops]) {
      expect(dest.menuGroupKey).toBe('configuration');
      expect(dest.navigationGroup).toBe('system');
    }
  });
});
