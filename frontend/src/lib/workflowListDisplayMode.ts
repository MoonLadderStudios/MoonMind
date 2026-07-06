import { workflowListContextParams } from './workflowListContext';

export type WorkflowListDisplayMode = 'hidden' | 'sidebar' | 'table';

export type WorkflowListRegion = 'none' | 'sidebar' | 'primary-surface';

export type WorkflowListDisplayModeDefinition = {
  value: WorkflowListDisplayMode;
  label: string;
  icon: 'Square' | 'PanelLeft' | 'Rows3';
  listRegion: WorkflowListRegion;
};

export type WorkflowListDisplaySurface =
  | 'workflows-table'
  | 'workflow-detail'
  | 'workflow-start';

export type WorkflowListSelection = {
  workflowId: string | null;
  source: 'route' | 'last-selected' | 'first-visible-row' | 'none';
};

export type WorkflowListDisplayRouteAction =
  | 'none'
  | 'navigate-workflows'
  | 'navigate-selected-detail'
  | 'resolve-first-workflow';

export type WorkflowListDisplayPrimarySurface =
  | 'workflow-detail'
  | 'workflow-start'
  | 'workflow-table'
  | 'empty-workflows';

export type WorkflowListDisplayListSurface = 'none' | 'sidebar' | 'table';

export type ResolvedWorkflowListDisplay = {
  requestedMode: WorkflowListDisplayMode;
  effectiveMode: WorkflowListDisplayMode;
  surface: WorkflowListDisplaySurface;
  routeAction: WorkflowListDisplayRouteAction;
  primarySurface: WorkflowListDisplayPrimarySurface;
  listSurface: WorkflowListDisplayListSurface;
  selection: WorkflowListSelection;
  targetPath: string;
  status: string | null;
};

export type ResolveWorkflowListDisplayInput = {
  pathname: string;
  requestedMode: WorkflowListDisplayMode;
  search?: string | URLSearchParams | null;
  selectedWorkflowId?: string | null;
  firstVisibleWorkflowId?: string | null;
};

const DETAIL_TABS = new Set(['steps', 'artifacts', 'runs', 'debug']);

export const WORKFLOW_LIST_DISPLAY_MODES = [
  {
    value: 'hidden',
    label: 'No list',
    icon: 'Square',
    listRegion: 'none',
  },
  {
    value: 'sidebar',
    label: 'Sidebar list',
    icon: 'PanelLeft',
    listRegion: 'sidebar',
  },
  {
    value: 'table',
    label: 'Full screen table',
    icon: 'Rows3',
    listRegion: 'primary-surface',
  },
] as const satisfies readonly WorkflowListDisplayModeDefinition[];

export function workflowListDisplayModeByValue(
  value: string,
): WorkflowListDisplayModeDefinition | null {
  return WORKFLOW_LIST_DISPLAY_MODES.find((mode) => mode.value === value) ?? null;
}

export function isWorkflowListDisplayMode(value: string): value is WorkflowListDisplayMode {
  return workflowListDisplayModeByValue(value) !== null;
}

export function readWorkflowListDisplayMode(payload: { initialData?: unknown }): WorkflowListDisplayMode | undefined {
  const raw = payload.initialData as { workflowListDisplayMode?: unknown } | undefined;
  const value = typeof raw?.workflowListDisplayMode === 'string' ? raw.workflowListDisplayMode : '';
  return isWorkflowListDisplayMode(value) ? value : undefined;
}

function normalizedPathname(pathname: string): string {
  const rawPath = pathname || '/';
  return rawPath.length > 1 && rawPath.endsWith('/') ? rawPath.slice(0, -1) : rawPath;
}

function normalizedSearch(search: string | URLSearchParams | null | undefined): string {
  if (!search) {
    return '';
  }
  const query = typeof search === 'string' ? search.replace(/^\?/, '') : search.toString();
  return query ? `?${query}` : '';
}

function pathWithSearch(pathname: string, search: string | URLSearchParams | null | undefined): string {
  return `${pathname}${normalizedSearch(search)}`;
}

function workflowListPathFromContext(search: string | URLSearchParams | null | undefined): string {
  const source = typeof search === 'string' ? new URLSearchParams(search.replace(/^\?/, '')) : search;
  const params = workflowListContextParams(source ?? new URLSearchParams());
  const query = params.toString();
  return query ? `/workflows?${query}` : '/workflows';
}

function encodeWorkflowDetailPath(workflowId: string, search: string | URLSearchParams | null | undefined): string {
  return pathWithSearch(`/workflows/${encodeURIComponent(workflowId)}`, search);
}

function decodeWorkflowDetail(pathname: string): { workflowId: string; subroute: string | null } | null {
  const parts = normalizedPathname(pathname).split('/').slice(1);
  if (parts.length !== 2 && parts.length !== 3) {
    return null;
  }
  if (parts[0] !== 'workflows' || parts[1] === 'new') {
    return null;
  }
  let workflowId: string;
  try {
    workflowId = decodeURIComponent(parts[1] ?? '');
  } catch {
    return null;
  }
  if (!workflowId || workflowId.includes('/')) {
    return null;
  }
  const subroute = parts[2] ?? null;
  if (subroute !== null && !DETAIL_TABS.has(subroute)) {
    return null;
  }
  return { workflowId, subroute };
}

function selectedWorkflow(input: ResolveWorkflowListDisplayInput): WorkflowListSelection {
  const selected = input.selectedWorkflowId?.trim();
  if (selected) {
    return { workflowId: selected, source: 'last-selected' };
  }
  const first = input.firstVisibleWorkflowId?.trim();
  if (first) {
    return { workflowId: first, source: 'first-visible-row' };
  }
  return { workflowId: null, source: 'none' };
}

export function resolveWorkflowListDisplay(
  input: ResolveWorkflowListDisplayInput,
): ResolvedWorkflowListDisplay | null {
  const pathname = normalizedPathname(input.pathname);
  const requestedMode = input.requestedMode;

  if (pathname === '/workflows') {
    if (requestedMode === 'table') {
      return {
        requestedMode,
        effectiveMode: 'table',
        surface: 'workflows-table',
        routeAction: 'none',
        primarySurface: 'workflow-table',
        listSurface: 'table',
        selection: { workflowId: null, source: 'none' },
        targetPath: pathWithSearch('/workflows', input.search),
        status: null,
      };
    }

    const selection = selectedWorkflow(input);
    if (selection.workflowId) {
      return {
        requestedMode,
        effectiveMode: requestedMode,
        surface: 'workflows-table',
        routeAction: selection.source === 'first-visible-row'
          ? 'resolve-first-workflow'
          : 'navigate-selected-detail',
        primarySurface: 'workflow-detail',
        listSurface: requestedMode === 'hidden' ? 'none' : 'sidebar',
        selection,
        targetPath: encodeWorkflowDetailPath(selection.workflowId, input.search),
        status: null,
      };
    }

    return {
      requestedMode,
      effectiveMode: 'table',
      surface: 'workflows-table',
      routeAction: 'none',
      primarySurface: 'empty-workflows',
      listSurface: 'table',
      selection,
      targetPath: pathWithSearch('/workflows', input.search),
      status: 'No workflow can be opened from the current list.',
    };
  }

  if (pathname === '/workflows/new') {
    if (requestedMode === 'table') {
      return {
        requestedMode,
        effectiveMode: 'table',
        surface: 'workflow-start',
        routeAction: 'navigate-workflows',
        primarySurface: 'workflow-table',
        listSurface: 'table',
        selection: { workflowId: null, source: 'none' },
        targetPath: '/workflows',
        status: null,
      };
    }
    return {
      requestedMode,
      effectiveMode: requestedMode,
      surface: 'workflow-start',
      routeAction: 'none',
      primarySurface: 'workflow-start',
      listSurface: requestedMode === 'sidebar' ? 'sidebar' : 'none',
      selection: { workflowId: null, source: 'none' },
      targetPath: pathWithSearch('/workflows/new', input.search),
      status: null,
    };
  }

  const detail = decodeWorkflowDetail(pathname);
  if (!detail) {
    return null;
  }

  if (requestedMode === 'table') {
    return {
      requestedMode,
      effectiveMode: 'table',
      surface: 'workflow-detail',
      routeAction: 'navigate-workflows',
      primarySurface: 'workflow-table',
      listSurface: 'table',
      selection: { workflowId: detail.workflowId, source: 'route' },
      targetPath: workflowListPathFromContext(input.search),
      status: null,
    };
  }

  return {
    requestedMode,
    effectiveMode: requestedMode,
    surface: 'workflow-detail',
    routeAction: 'none',
    primarySurface: 'workflow-detail',
    listSurface: requestedMode === 'hidden' ? 'none' : 'sidebar',
    selection: { workflowId: detail.workflowId, source: 'route' },
    targetPath: pathWithSearch(pathname, input.search),
    status: null,
  };
}
