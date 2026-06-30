import type { BootPayload } from '../boot/parseBootPayload';

export type DashboardPage =
  | 'index-health'
  | 'manifests'
  | 'oauth-terminal'
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
};

const DETAIL_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const WORKFLOW_ID_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._:{}-]{0,254}$/;
const WORKFLOW_DETAIL_TABS = new Set(['steps', 'artifacts', 'runs', 'debug']);

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
  if (path === '/workflows/new') {
    return { page: 'workflow-start', dataWidePanel: false, currentPath: path };
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
    return { page: 'schedules', dataWidePanel: false, currentPath: path };
  }
  if (path === '/manifests' || isDetailPath(path, 'manifests')) {
    return { page: 'manifests', dataWidePanel: false, currentPath: path };
  }
  if (path === '/index-health') {
    return { page: 'index-health', dataWidePanel: true, currentPath: path };
  }
  if (path === '/oauth-terminal') {
    return { page: 'oauth-terminal', dataWidePanel: true, currentPath: path };
  }
  return null;
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
