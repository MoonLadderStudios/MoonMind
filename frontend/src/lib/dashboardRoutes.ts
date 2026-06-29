import type { BootPayload } from '../boot/parseBootPayload';

export type DashboardPage =
  | 'index-health'
  | 'manifests'
  | 'oauth-terminal'
  | 'schedules'
  | 'settings'
  | 'skills'
  | 'workflow-start'
  | 'workflow-detail'
  | 'workflows-home'
  | 'workflow-list';

export type DashboardRoute = {
  page: DashboardPage;
  dataWidePanel: boolean;
  currentPath: string;
};

export type DashboardClientRouteConfig = {
  dashboardConfig?: unknown;
  settingsPermissions?: unknown;
  workerPause?: unknown;
};

const WORKFLOW_DETAIL_PATH = /^\/workflows\/[A-Za-z0-9][A-Za-z0-9._:{}-]{0,254}(?:\/(?:steps|artifacts|runs|debug))?$/;
const MANIFEST_DETAIL_PATH = /^\/manifests\/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SCHEDULE_DETAIL_PATH = /^\/schedules\/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function withoutTrailingSlash(pathname: string): string {
  return pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
}

function hasExtension(pathname: string): boolean {
  const leaf = pathname.split('/').pop() ?? '';
  return /\.[A-Za-z0-9]+$/.test(leaf);
}

export function resolveDashboardRoute(pathname: string): DashboardRoute | null {
  const path = withoutTrailingSlash(pathname || '/');
  if (hasExtension(path)) {
    return null;
  }
  if (path === '/workflows') {
    return { page: 'workflow-list', dataWidePanel: true, currentPath: path };
  }
  if (path === '/workflows/new') {
    return { page: 'workflow-start', dataWidePanel: false, currentPath: path };
  }
  if (WORKFLOW_DETAIL_PATH.test(path) && path !== '/workflows/new') {
    return { page: 'workflow-detail', dataWidePanel: false, currentPath: path };
  }
  if (path === '/settings' || path.startsWith('/settings/')) {
    return { page: 'settings', dataWidePanel: true, currentPath: path };
  }
  if (path === '/skills' || path.startsWith('/skills/')) {
    return { page: 'skills', dataWidePanel: false, currentPath: path };
  }
  if (path === '/schedules' || SCHEDULE_DETAIL_PATH.test(path)) {
    return { page: 'schedules', dataWidePanel: false, currentPath: path };
  }
  if (path === '/manifests' || MANIFEST_DETAIL_PATH.test(path)) {
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
  clientConfig?: DashboardClientRouteConfig | null,
): BootPayload {
  const initialData = baseInitialData(payload);
  const routeDashboardConfig = objectValue(clientConfig?.dashboardConfig);
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
    initialData.workerPause = objectValue(clientConfig?.workerPause) ?? initialData.workerPause ?? {
      get: '/api/system/worker-pause',
      post: '/api/system/worker-pause',
      shardHealth: '/api/workflows/codex/shards',
    };
    initialData.runtimeConfig = initialData.runtimeConfig ?? nextDashboardConfig;
    initialData.settingsPermissions =
      stringArrayValue(clientConfig?.settingsPermissions) ??
      stringArrayValue(initialData.settingsPermissions) ??
      [];
  }

  return {
    ...payload,
    page: route.page,
    initialData,
  };
}
