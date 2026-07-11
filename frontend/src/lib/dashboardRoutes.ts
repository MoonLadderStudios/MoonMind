import type { BootPayload } from '../boot/parseBootPayload';
import { WORKFLOW_DETAIL_SUPPORTED_SUBROUTES } from './workflowDetailRoutes';

export type DashboardPage =
  | 'artifacts'
  | 'index-health'
  | 'manifests'
  | 'omnigent-inventory'
  | 'oauth-terminal'
  | 'remediations'
  | 'schedules'
  | 'settings'
  | 'skills'
  | 'workflow-start'
  | 'workflows-workspace'
  | 'workflow-detail'
  | 'workflows-home'
  | 'workflow-list';

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
export type DashboardDisplayMode = 'workflow-list' | 'recurring-list';
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
};

export type DashboardDestination = DashboardDestinationInfo & {
  page: DashboardPage;
  dataWidePanel: boolean;
};

export const DASHBOARD_DESTINATIONS: readonly DashboardDestination[] = [
  { key: 'workflows', label: 'Workflows', iconKey: 'scroll-text', canonicalPath: '/workflows', pathPatterns: ['/workflows', '/workflows/:workflowId', '/workflows/:workflowId/:detailTab'], navigationGroup: 'primary', pageClassification: 'workspace', capabilityKey: 'workflowList', endpointKey: 'workflows', displayMode: 'workflow-list', page: 'workflows-workspace', dataWidePanel: true },
  { key: 'create', label: 'Create', iconKey: 'rocket', canonicalPath: '/workflows/new', pathPatterns: ['/workflows/new'], navigationGroup: 'primary', pageClassification: 'create', capabilityKey: 'workflowActions', page: 'workflows-workspace', dataWidePanel: true },
  { key: 'recurring', label: 'Recurring', iconKey: 'moon', canonicalPath: '/schedules', pathPatterns: ['/schedules', '/schedules/:definitionId'], navigationGroup: 'primary', pageClassification: 'workspace', capabilityKey: 'schedules', endpointKey: 'schedules', displayMode: 'recurring-list', page: 'schedules', dataWidePanel: true },
  { key: 'skills', label: 'Skills', iconKey: 'sparkles', canonicalPath: '/skills', pathPatterns: ['/skills/*'], navigationGroup: 'primary', pageClassification: 'workspace', capabilityKey: 'skills', endpointKey: 'skills', page: 'skills', dataWidePanel: false },
  { key: 'manifests', label: 'RAG / Manifests', iconKey: 'manifest', canonicalPath: '/manifests', pathPatterns: ['/manifests', '/manifests/:manifestName'], navigationGroup: 'operations', pageClassification: 'collection', capabilityKey: 'manifests', endpointKey: 'manifests', page: 'manifests', dataWidePanel: true },
  { key: 'omnigent-agents', label: 'Omnigent Agents', iconKey: 'bot', canonicalPath: '/omnigent/agents', pathPatterns: ['/omnigent/agents/*'], navigationGroup: 'operations', pageClassification: 'collection', capabilityKey: 'omnigentAgents', endpointKey: 'omnigentAgents', page: 'omnigent-inventory', dataWidePanel: true },
  { key: 'omnigent-policies', label: 'Omnigent Policies', iconKey: 'shield-check', canonicalPath: '/omnigent/policies', pathPatterns: ['/omnigent/policies/*'], navigationGroup: 'operations', pageClassification: 'collection', capabilityKey: 'omnigentPolicies', endpointKey: 'omnigentPolicies', page: 'omnigent-inventory', dataWidePanel: true },
  { key: 'remediation', label: 'Remediation', iconKey: 'wrench', canonicalPath: '/remediations', pathPatterns: ['/remediations/*'], navigationGroup: 'operations', pageClassification: 'collection', capabilityKey: 'remediationCollection', endpointKey: 'remediations', page: 'remediations', dataWidePanel: true },
  { key: 'artifacts', label: 'Artifacts / Observability', iconKey: 'archive', canonicalPath: '/artifacts', pathPatterns: ['/artifacts/*', '/observability/*'], navigationGroup: 'operations', pageClassification: 'collection', capabilityKey: 'artifacts', endpointKey: 'artifacts', page: 'artifacts', dataWidePanel: true },
  { key: 'settings', label: 'Settings', iconKey: 'settings', canonicalPath: '/settings', pathPatterns: ['/settings/*'], navigationGroup: 'system', pageClassification: 'utility', capabilityKey: 'settings', endpointKey: 'settings', page: 'settings', dataWidePanel: true },
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
  return visibleDashboardDestinations(uiInfo).filter(({ navigationGroup }) => navigationGroup === 'primary');
}

export function visibleSystemDestinations(
  uiInfo: DashboardUiInfo | null | undefined,
): DashboardDestination[] {
  return visibleDashboardDestinations(uiInfo).filter(({ navigationGroup }) => navigationGroup !== 'primary');
}

export const DASHBOARD_REACT_ROUTE_PATHS = Array.from(
  new Set(DASHBOARD_DESTINATIONS.flatMap((destination) => destination.pathPatterns)),
);

export function matchesDashboardDestinationRegistry(
  destinations: DashboardDestinationInfo[] | undefined,
): boolean {
  if (!destinations || destinations.length !== DASHBOARD_DESTINATIONS.length) return false;
  return DASHBOARD_DESTINATIONS.every((local, index) => {
    const remote = destinations[index];
    if (!remote) return false;
    return (
      local.key === remote.key &&
      local.label === remote.label &&
      local.iconKey === remote.iconKey &&
      local.canonicalPath === remote.canonicalPath &&
      local.navigationGroup === remote.navigationGroup &&
      local.pageClassification === remote.pageClassification &&
      local.capabilityKey === remote.capabilityKey &&
      local.endpointKey === remote.endpointKey &&
      local.displayMode === remote.displayMode &&
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

function pathParts(path: string): string[] {
  return path.split('/').slice(1);
}

function isWorkflowDetailPath(path: string): boolean {
  const parts = pathParts(path);
  if (parts.length !== 2 && parts.length !== 3) {
    return false;
  }
  if (parts[0] !== 'workflows') {
    return false;
  }
  const workflowIdSegment = parts[1];
  if (!workflowIdSegment) {
    return false;
  }
  const workflowId = decodePathSegment(workflowIdSegment);
  if (!workflowId || !WORKFLOW_ID_SEGMENT.test(workflowId)) {
    return false;
  }
  if (parts.length === 2) {
    return true;
  }
  const tabSegment = parts[2];
  if (!tabSegment) {
    return false;
  }
  const tab = decodePathSegment(tabSegment);
  return Boolean(tab && WORKFLOW_DETAIL_TABS.has(tab));
}

function isDetailPath(path: string, prefix: 'manifests' | 'schedules'): boolean {
  const parts = pathParts(path);
  if (parts.length !== 2 || parts[0] !== prefix) {
    return false;
  }
  const detailIdSegment = parts[1];
  if (!detailIdSegment) {
    return false;
  }
  const detailId = decodePathSegment(detailIdSegment);
  return Boolean(detailId && DETAIL_SEGMENT.test(detailId));
}

export function resolveDashboardRoute(pathname: string): DashboardRoute | null {
  const path = withoutTrailingSlash(pathname || '/');
  if (hasExtension(path)) {
    return null;
  }
  if (path === '/workflows') {
    return { page: 'workflows-workspace', dataWidePanel: true, currentPath: path };
  }
  if (path === '/artifacts' || path.startsWith('/artifacts/') || path === '/observability' || path.startsWith('/observability/')) {
    return { page: 'artifacts', dataWidePanel: true, currentPath: path };
  }
  if (path === '/workflows/new') {
    return { page: 'workflows-workspace', dataWidePanel: true, currentPath: path };
  }
  if (isWorkflowDetailPath(path) && path !== '/workflows/new') {
    return { page: 'workflows-workspace', dataWidePanel: true, currentPath: path };
  }
  if (path === '/settings' || path.startsWith('/settings/')) {
    return { page: 'settings', dataWidePanel: true, currentPath: path };
  }
  if (path === '/skills' || path.startsWith('/skills/')) {
    return { page: 'skills', dataWidePanel: false, currentPath: path };
  }
  if (path === '/schedules' || isDetailPath(path, 'schedules')) {
    return { page: 'schedules', dataWidePanel: true, currentPath: path };
  }
  if (path === '/manifests' || isDetailPath(path, 'manifests')) {
    return { page: 'manifests', dataWidePanel: true, currentPath: path };
  }
  if (path === '/omnigent/agents' || path.startsWith('/omnigent/agents/') || path === '/omnigent/policies' || path.startsWith('/omnigent/policies/')) {
    return { page: 'omnigent-inventory', dataWidePanel: true, currentPath: path };
  }
  if (path === '/index-health') {
    return { page: 'index-health', dataWidePanel: true, currentPath: path };
  }
  if (path === '/remediations' || path.startsWith('/remediations/')) {
    return { page: 'remediations', dataWidePanel: true, currentPath: path };
  }
  if (path === '/oauth-terminal') {
    return { page: 'oauth-terminal', dataWidePanel: true, currentPath: path };
  }
  return null;
}

export function destinationForPath(pathname: string): DashboardDestination | null {
  const route = resolveDashboardRoute(pathname);
  if (!route) return null;
  if (pathname === '/workflows/new') return DASHBOARD_DESTINATIONS[1] ?? null;
  return DASHBOARD_DESTINATIONS.find((destination) => (
    destination.page === route.page && (
      destination.key !== 'artifacts' || pathname.startsWith('/artifacts') || pathname.startsWith('/observability')
    ) && (
      destination.key !== 'omnigent-agents' || pathname.startsWith('/omnigent/agents')
    ) && (
      destination.key !== 'omnigent-policies' || pathname.startsWith('/omnigent/policies')
    )
  )) ?? null;
}

export function isDashboardInternalUrl(url: URL): boolean {
  return url.origin === window.location.origin && resolveDashboardRoute(url.pathname) !== null;
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

  if (route.page === 'settings') {
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
