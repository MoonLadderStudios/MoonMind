import type { BootPayload } from '../boot/parseBootPayload';
import { WORKFLOW_DETAIL_SUPPORTED_SUBROUTES } from './workflowDetailRoutes';

export type DashboardPage =
  | 'artifacts'
  | 'omnigent-inventory'
  | 'oauth-terminal'
  | 'remediations'
  | 'schedules'
  | 'settings-entry'
  | 'settings-operations'
  | 'settings-providers-secrets'
  | 'settings-instance'
  | 'skills'
  | 'workflows-workspace';

export type DashboardRoute = {
  page: DashboardPage;
  dataWidePanel: boolean;
  currentPath: string;
};

export type DashboardUiInfo = {
  app?: string;
  buildId?: string | null;
  apiBase?: string;
  features?: Record<string, unknown>;
  limits?: Record<string, unknown>;
  endpoints?: Record<string, unknown>;
  dashboardConfig?: unknown;
  settingsPermissions?: unknown;
  workerPause?: unknown;
  destinations?: DashboardDestinationInfo[];
};

export type DashboardNavigationGroup = 'primary' | 'operations' | 'system';
export type DashboardPageClassification = 'collection' | 'create' | 'workspace' | 'utility';
export type DashboardDisplayMode = 'workflow-list' | 'recurring-list' | 'skills-list';
export type DashboardIconKey =
  | 'archive' | 'bot' | 'manifest' | 'moon' | 'rocket' | 'scroll-text'
  | 'settings' | 'shield-check' | 'sparkles' | 'wrench';

export type DashboardDestinationInfo = {
  key: string;
  label: string;
  iconKey: DashboardIconKey;
  canonicalPath: string;
  pathPatterns: string[];
  navigationGroup: DashboardNavigationGroup;
  pageClassification: DashboardPageClassification;
  capabilityKey: string;
  endpointKey?: string;
  displayMode?: DashboardDisplayMode;
  menuGroupKey?: string;
};

export type DashboardDestination = DashboardDestinationInfo & {
  page: DashboardPage;
  dataWidePanel: boolean;
};

export type DashboardDestinationGroup = {
  key: string;
  label: string;
  triggerLabel: string;
  triggerIconKey: DashboardIconKey;
  destinationKeys: readonly string[];
};

export const DASHBOARD_DESTINATION_GROUPS: readonly DashboardDestinationGroup[] = [
  {
    key: 'configuration',
    label: 'Configuration',
    triggerLabel: 'Settings',
    triggerIconKey: 'settings',
    destinationKeys: [
      'settings-providers-secrets',
      'settings-instance',
      'settings-operations',
    ],
  },
];

export const DASHBOARD_DESTINATIONS: readonly DashboardDestination[] = [
  { key: 'workflows', label: 'Workflows', iconKey: 'scroll-text', canonicalPath: '/workflows', pathPatterns: ['/workflows', '/workflows/:workflowId', '/workflows/:workflowId/:detailTab'], navigationGroup: 'primary', pageClassification: 'workspace', capabilityKey: 'workflowList', endpointKey: 'workflows', displayMode: 'workflow-list', page: 'workflows-workspace', dataWidePanel: true },
  { key: 'create', label: 'Create', iconKey: 'rocket', canonicalPath: '/workflows/new', pathPatterns: ['/workflows/new'], navigationGroup: 'primary', pageClassification: 'create', capabilityKey: 'workflowActions', page: 'workflows-workspace', dataWidePanel: true },
  { key: 'recurring', label: 'Recurring', iconKey: 'moon', canonicalPath: '/schedules', pathPatterns: ['/schedules', '/schedules/:definitionId'], navigationGroup: 'system', pageClassification: 'workspace', capabilityKey: 'schedules', endpointKey: 'schedules', displayMode: 'recurring-list', page: 'schedules', dataWidePanel: true },
  { key: 'skills', label: 'Skills', iconKey: 'sparkles', canonicalPath: '/skills', pathPatterns: ['/skills/*'], navigationGroup: 'system', pageClassification: 'workspace', capabilityKey: 'skills', endpointKey: 'skills', displayMode: 'skills-list', page: 'skills', dataWidePanel: true },
  { key: 'omnigent-agents', label: 'Agents', iconKey: 'bot', canonicalPath: '/omnigent/agents', pathPatterns: ['/omnigent/agents/*'], navigationGroup: 'operations', pageClassification: 'collection', capabilityKey: 'omnigentAgents', endpointKey: 'omnigentAgents', page: 'omnigent-inventory', dataWidePanel: true },
  { key: 'omnigent-policies', label: 'Policies', iconKey: 'shield-check', canonicalPath: '/omnigent/policies', pathPatterns: ['/omnigent/policies/*'], navigationGroup: 'operations', pageClassification: 'collection', capabilityKey: 'omnigentPolicies', endpointKey: 'omnigentPolicies', page: 'omnigent-inventory', dataWidePanel: true },
  { key: 'remediation', label: 'Remediation', iconKey: 'wrench', canonicalPath: '/remediations', pathPatterns: ['/remediations/*'], navigationGroup: 'operations', pageClassification: 'collection', capabilityKey: 'remediationCollection', endpointKey: 'remediations', page: 'remediations', dataWidePanel: true },
  { key: 'artifacts', label: 'Artifacts', iconKey: 'archive', canonicalPath: '/artifacts', pathPatterns: ['/artifacts/*', '/observability/*'], navigationGroup: 'operations', pageClassification: 'collection', capabilityKey: 'artifacts', endpointKey: 'artifacts', page: 'artifacts', dataWidePanel: true },
  { key: 'settings-providers-secrets', label: 'Providers & Secrets', iconKey: 'settings', canonicalPath: '/settings/providers-secrets', pathPatterns: ['/settings/providers-secrets'], navigationGroup: 'system', pageClassification: 'utility', capabilityKey: 'settingsProvidersSecrets', endpointKey: 'settings', menuGroupKey: 'configuration', page: 'settings-providers-secrets', dataWidePanel: true },
  { key: 'settings-instance', label: 'Instance', iconKey: 'settings', canonicalPath: '/settings/instance', pathPatterns: ['/settings/instance'], navigationGroup: 'system', pageClassification: 'utility', capabilityKey: 'settingsInstance', endpointKey: 'settings', menuGroupKey: 'configuration', page: 'settings-instance', dataWidePanel: true },
  { key: 'settings-operations', label: 'Operations', iconKey: 'settings', canonicalPath: '/settings/operations', pathPatterns: ['/settings/operations'], navigationGroup: 'system', pageClassification: 'utility', capabilityKey: 'settingsOperations', endpointKey: 'settings', menuGroupKey: 'configuration', page: 'settings-operations', dataWidePanel: true },
];

export type DashboardDestinationState = 'shown' | 'hidden' | 'unavailable';

export function destinationState(
  destination: DashboardDestination,
  uiInfo: DashboardUiInfo | null | undefined,
): DashboardDestinationState {
  const value = uiInfo?.features?.[destination.capabilityKey];
  if (value === true) return 'shown';
  if (value === false) return 'unavailable';
  return 'hidden';
}

export function visibleDashboardDestinations(
  uiInfo: DashboardUiInfo | null | undefined,
): DashboardDestination[] {
  return DASHBOARD_DESTINATIONS.filter((destination) => destinationState(destination, uiInfo) === 'shown');
}

export function visiblePrimaryDestinations(
  uiInfo: DashboardUiInfo | null | undefined,
): DashboardDestination[] {
  if (!uiInfo) {
    return DASHBOARD_DESTINATIONS.filter(({ navigationGroup }) => navigationGroup === 'primary');
  }
  return visibleDashboardDestinations(uiInfo).filter(({ navigationGroup }) => navigationGroup === 'primary');
}

export function visibleSystemDestinations(
  uiInfo: DashboardUiInfo | null | undefined,
): DashboardDestination[] {
  return visibleDashboardDestinations(uiInfo).filter(({ navigationGroup }) => navigationGroup !== 'primary');
}

export function exposedSystemDestinations(
  uiInfo: DashboardUiInfo | null | undefined,
): DashboardDestination[] {
  return DASHBOARD_DESTINATIONS.filter((destination) => {
    if (destination.navigationGroup === 'primary') return false;
    const state = destinationState(destination, uiInfo);
    return state === 'shown' || (
      state === 'unavailable' && destination.menuGroupKey === 'configuration'
    );
  });
}

export function destinationGroupForDestination(
  destination: DashboardDestination | null | undefined,
): DashboardDestinationGroup | null {
  if (!destination?.menuGroupKey) return null;
  return DASHBOARD_DESTINATION_GROUPS.find(({ key }) => key === destination.menuGroupKey) ?? null;
}

export const DASHBOARD_REACT_ROUTE_PATHS = Array.from(
  new Set([
    ...DASHBOARD_DESTINATIONS.flatMap((destination) => destination.pathPatterns),
    '/settings',
    '/settings/*',
  ]),
);

/**
 * Detects version skew between this bundle and the server. Only the contract
 * the two sides share is compared — destination keys, paths, capability keys
 * and endpoint keys — so a wording or icon change on either side is not
 * reported as skew.
 */
export function matchesDashboardDestinationRegistry(
  destinations: DashboardDestinationInfo[] | undefined,
): boolean {
  if (!destinations || destinations.length !== DASHBOARD_DESTINATIONS.length) return false;
  const remoteByKey = new Map(destinations.map((remote) => [remote.key, remote]));
  return DASHBOARD_DESTINATIONS.every((local) => {
    const remote = remoteByKey.get(local.key);
    return Boolean(remote) && (
      local.canonicalPath === remote?.canonicalPath &&
      local.capabilityKey === remote.capabilityKey &&
      local.endpointKey === remote.endpointKey &&
      JSON.stringify(local.pathPatterns) === JSON.stringify(remote.pathPatterns)
    );
  });
}

const DETAIL_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const WORKFLOW_ID_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._:{}-]{0,254}$/;
const WORKFLOW_DETAIL_TABS = new Set<string>(WORKFLOW_DETAIL_SUPPORTED_SUBROUTES);

function withoutTrailingSlash(pathname: string): string {
  return pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
}

function hasExtension(pathname: string): boolean {
  const leaf = pathname.split('/').pop() ?? '';
  return /\.[A-Za-z0-9]+$/.test(leaf);
}

function decodePathSegment(segment: string): string | null {
  try {
    const decoded = decodeURIComponent(segment);
    return decoded.includes('/') ? null : decoded;
  } catch {
    return null;
  }
}

/**
 * Routes that are not navigation destinations but still render a page. Every
 * other route comes from {@link DASHBOARD_DESTINATIONS}.
 */
const DASHBOARD_UTILITY_ROUTES: readonly { path: string; page: DashboardPage }[] = [
  { path: '/settings', page: 'settings-entry' },
  { path: '/oauth-terminal', page: 'oauth-terminal' },
];

const ROUTE_PARAM_VALIDATORS: Record<string, (value: string) => boolean> = {
  workflowId: (value) => WORKFLOW_ID_SEGMENT.test(value),
  detailTab: (value) => WORKFLOW_DETAIL_TABS.has(value),
  definitionId: (value) => DETAIL_SEGMENT.test(value),
};

function isDynamicPattern(pattern: string): boolean {
  return pattern.includes(':') || pattern.endsWith('/*');
}

function matchesPathPattern(path: string, pattern: string): boolean {
  if (pattern.endsWith('/*')) {
    const base = pattern.slice(0, -2);
    return path === base || path.startsWith(`${base}/`);
  }
  const patternParts = pattern.split('/');
  const parts = path.split('/');
  if (patternParts.length !== parts.length) return false;
  return patternParts.every((patternPart, index) => {
    const part = parts[index] ?? '';
    if (!patternPart.startsWith(':')) return patternPart === part;
    const value = decodePathSegment(part);
    const validate = ROUTE_PARAM_VALIDATORS[patternPart.slice(1)];
    return Boolean(value && validate?.(value));
  });
}

/** The destination owning a normalized path; static paths win over dynamic ones. */
function matchDestination(path: string): DashboardDestination | null {
  for (const dynamic of [false, true]) {
    const match = DASHBOARD_DESTINATIONS.find((destination) => destination.pathPatterns.some(
      (pattern) => isDynamicPattern(pattern) === dynamic && matchesPathPattern(path, pattern),
    ));
    if (match) return match;
  }
  return null;
}

function normalizedRoutePath(pathname: string): string | null {
  const path = withoutTrailingSlash(pathname || '/');
  return hasExtension(path) ? null : path;
}

export function resolveDashboardRoute(pathname: string): DashboardRoute | null {
  const path = normalizedRoutePath(pathname);
  if (!path) return null;
  const destination = matchDestination(path);
  if (destination) {
    return { page: destination.page, dataWidePanel: destination.dataWidePanel, currentPath: path };
  }
  const utility = DASHBOARD_UTILITY_ROUTES.find((route) => route.path === path);
  return utility ? { page: utility.page, dataWidePanel: true, currentPath: path } : null;
}

export function destinationForPath(pathname: string): DashboardDestination | null {
  const path = normalizedRoutePath(pathname);
  return path ? matchDestination(path) : null;
}

const LEGACY_SETTINGS_REDIRECTS: Record<string, string> = {
  '/secrets': '/settings/providers-secrets',
  '/workers': '/settings/operations',
  // Retired account-scoped route (MoonMind#4353): existing
  // `/settings/user-workspace` bookmarks land on the Instance replacement
  // with stale human-scope params filtered by the target allowlist below.
  '/settings/user-workspace': '/settings/instance',
};

// Only preserve page-relevant filters per target. The Instance page keeps
// no URL-owned filters: stale human-scope params (scope, q) are dropped
// rather than reused after the account-free cutover.
const SETTINGS_QUERY_ALLOWLIST_BY_TARGET: Record<string, Set<string>> = {
  '/settings/providers-secrets': new Set(['runtime']),
  '/settings/operations': new Set(['status']),
  '/settings/instance': new Set([]),
};

export function filterSettingsQueryForTarget(search: string, target: string): string {
  const params = new URLSearchParams(search);
  params.delete('section');
  const allowed = SETTINGS_QUERY_ALLOWLIST_BY_TARGET[target];
  if (allowed) {
    for (const key of Array.from(params.keys())) {
      if (!allowed.has(key)) params.delete(key);
    }
  } else {
    // Fallback: drop all except section already removed -> empty.
    for (const key of Array.from(params.keys())) params.delete(key);
  }
  const query = params.toString();
  return query ? `${target}?${query}` : target;
}

export function legacySettingsRedirect(pathname: string, search: string): string | null {
  const normalized = withoutTrailingSlash(pathname);
  const canonical = LEGACY_SETTINGS_REDIRECTS[normalized];
  if (!canonical) return null;
  if (!search) return canonical;
  return filterSettingsQueryForTarget(search, canonical);
}

const LEGACY_SETTINGS_CAPABILITY_BY_PATH: Record<string, string> = {
  '/settings/providers-secrets': 'settingsProvidersSecrets',
  '/settings/instance': 'settingsInstance',
  '/settings/operations': 'settingsOperations',
};

const LEGACY_SETTINGS_FALLBACK_ORDER: readonly string[] = [
  '/settings/providers-secrets',
  '/settings/instance',
  '/settings/operations',
];

export type LegacySettingsTarget =
  | { status: 'none' }
  | { status: 'pending' }
  | { status: 'redirect'; target: string };

export function resolveAuthorizedLegacySettingsTarget(
  pathname: string,
  search: string,
  uiInfo: DashboardUiInfo | null,
  isUiInfoPending: boolean,
): LegacySettingsTarget {
  const rawTarget = legacySettingsRedirect(pathname, search);
  if (!rawTarget) return { status: 'none' };
  // Permission-aware fallback: never land on an unauthorized destination.
  // Mimics the backend _settings_redirect_path ordering.
  if (uiInfo && !isUiInfoPending) {
    const canonicalPath = rawTarget.split('?')[0] ?? rawTarget;
    const preferredCapability = LEGACY_SETTINGS_CAPABILITY_BY_PATH[canonicalPath];
    const isShown = (key: string) => uiInfo.features?.[key] === true;
    if (preferredCapability && !isShown(preferredCapability)) {
      const preferredIndex = LEGACY_SETTINGS_FALLBACK_ORDER.indexOf(canonicalPath);
      const orderedFallback = preferredIndex >= 0
        ? [canonicalPath, ...LEGACY_SETTINGS_FALLBACK_ORDER.filter((p) => p !== canonicalPath)]
        : [...LEGACY_SETTINGS_FALLBACK_ORDER];
      for (const path of orderedFallback) {
        const cap = LEGACY_SETTINGS_CAPABILITY_BY_PATH[path];
        if (cap && isShown(cap)) {
          // Re-filter the original query for the resolved target: the raw
          // target already discarded parameters that are valid for the
          // fallback destination.
          return { status: 'redirect', target: filterSettingsQueryForTarget(search, path) };
        }
      }
      return { status: 'redirect', target: '/settings' };
    }
    return { status: 'redirect', target: rawTarget };
  }
  if (isUiInfoPending) {
    // Wait for capability data before deciding the fallback; the caller
    // renders a loading shell to avoid a flash of unauthorized redirect.
    return { status: 'pending' };
  }
  return { status: 'redirect', target: rawTarget };
}

export function isDashboardInternalUrl(url: URL): boolean {
  if (url.origin !== window.location.origin) return false;
  if (resolveDashboardRoute(url.pathname) !== null) return true;
  return legacySettingsRedirect(url.pathname, url.search) !== null;
}

function baseInitialData(payload: BootPayload): Record<string, unknown> {
  const raw = payload.initialData;
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? { ...(raw as Record<string, unknown>) } : {};
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? { ...(value as Record<string, unknown>) }
    : null;
}

function stringArrayValue(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
    ? [...value]
    : null;
}

export function payloadForDashboardRoute(
  payload: BootPayload,
  route: DashboardRoute,
  uiInfo?: DashboardUiInfo | null,
): BootPayload {
  const initialData = baseInitialData(payload);
  const routeDashboardConfig = objectValue(uiInfo?.dashboardConfig);
  const dashboardConfig =
    routeDashboardConfig ??
    (initialData.dashboardConfig && typeof initialData.dashboardConfig === 'object'
      ? { ...(initialData.dashboardConfig as Record<string, unknown>) }
      : null);
  const nextDashboardConfig = dashboardConfig
      ? { ...dashboardConfig, initialPath: route.currentPath }
      : { initialPath: route.currentPath };
  const layout =
    initialData.layout && typeof initialData.layout === 'object'
      ? { ...(initialData.layout as Record<string, unknown>) }
      : {};
  layout.dataWidePanel = route.dataWidePanel;
  initialData.dashboardConfig = nextDashboardConfig;
  initialData.layout = layout;
  initialData.uiEndpoints = objectValue(uiInfo?.endpoints) ?? {};

  if (route.page.startsWith('settings-')) {
    initialData.workerPause = objectValue(uiInfo?.workerPause) ?? initialData.workerPause ?? {
      get: '/api/system/worker-pause',
      post: '/api/system/worker-pause',
      shardHealth: '/api/workflows/codex/shards',
    };
    initialData.runtimeConfig = initialData.runtimeConfig ?? nextDashboardConfig;
    initialData.settingsPermissions =
      stringArrayValue(uiInfo?.settingsPermissions) ??
      stringArrayValue(initialData.settingsPermissions) ??
      [];
  }

  return {
    ...payload,
    page: route.page,
    apiBase: typeof uiInfo?.apiBase === 'string' ? uiInfo.apiBase : payload.apiBase,
    features:
      uiInfo?.features && typeof uiInfo.features === 'object'
        ? (uiInfo.features as Record<string, boolean>)
        : payload.features,
    initialData,
  };
}
