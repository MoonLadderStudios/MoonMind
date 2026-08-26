import { workflowListContextParams } from './workflowListContext';
import { WORKFLOW_DETAIL_SUPPORTED_SUBROUTES } from './workflowDetailRoutes';

/** Entity-neutral presentation shared by every opted-in collection workspace. */
export type CollectionListDisplayMode = 'hidden' | 'sidebar' | 'table';

export type CollectionListRegion = 'none' | 'sidebar' | 'primary-surface';

export type CollectionListDisplayModeDefinition = {
  value: CollectionListDisplayMode;
  label: string;
  icon: 'Square' | 'PanelLeft' | 'Rows3';
  listRegion: CollectionListRegion;
};

export type WorkflowListDisplaySurface =
  | 'workflows-table'
  | 'workflow-detail'
  | 'workflow-start';

export type WorkflowListSelection = {
  workflowId: string | null;
  source: 'route' | 'last-selected' | 'first-visible-row' | 'none';
};

export type CollectionListDisplayRouteAction =
  | 'none'
  | 'navigate-workflows'
  | 'navigate-recurring'
  | 'navigate-skills'
  | 'navigate-selected-detail'
  | 'resolve-first-row';

export type WorkflowListDisplayPrimarySurface =
  | 'workflow-detail'
  | 'workflow-start'
  | 'workflow-table'
  | 'empty-workflows';

export type CollectionListDisplayListSurface = 'none' | 'sidebar' | 'table';

export type ResolvedWorkflowListDisplay = {
  requestedMode: CollectionListDisplayMode;
  effectiveMode: CollectionListDisplayMode;
  surface: WorkflowListDisplaySurface;
  routeAction: CollectionListDisplayRouteAction;
  primarySurface: WorkflowListDisplayPrimarySurface;
  listSurface: CollectionListDisplayListSurface;
  selection: WorkflowListSelection;
  targetPath: string;
  status: string | null;
};

export type RecurringListDisplaySurface =
  | 'recurring-table'
  | 'recurring-detail'
  | 'future-recurring-create';

export type RecurringListSelection = {
  definitionId: string | null;
  source: 'route' | 'last-selected' | 'first-visible-row' | 'none';
};

export type ResolvedRecurringListDisplay = {
  requestedMode: CollectionListDisplayMode;
  effectiveMode: CollectionListDisplayMode;
  surface: RecurringListDisplaySurface;
  routeAction: CollectionListDisplayRouteAction;
  primarySurface: 'recurring-detail' | 'recurring-table' | 'empty-recurring';
  listSurface: CollectionListDisplayListSurface;
  selection: RecurringListSelection;
  targetPath: string;
  status: string | null;
};

export type ResolveRecurringListDisplayInput = {
  pathname: string;
  requestedMode: CollectionListDisplayMode;
  search?: string | URLSearchParams | null;
  selectedDefinitionId?: string | null;
  firstVisibleDefinitionId?: string | null;
};

export type SkillListDisplaySurface = 'skills-table' | 'skill-detail';

export type SkillListSelection = {
  skillId: string | null;
  source: 'route' | 'last-selected' | 'first-visible-row' | 'none';
};

export type ResolvedSkillListDisplay = {
  requestedMode: CollectionListDisplayMode;
  effectiveMode: CollectionListDisplayMode;
  surface: SkillListDisplaySurface;
  routeAction: CollectionListDisplayRouteAction;
  primarySurface: 'skill-detail' | 'skill-table' | 'empty-skills';
  listSurface: CollectionListDisplayListSurface;
  selection: SkillListSelection;
  targetPath: string;
  status: string | null;
};

export type ResolveSkillListDisplayInput = {
  pathname: string;
  requestedMode: CollectionListDisplayMode;
  search?: string | URLSearchParams | null;
  selectedSkillId?: string | null;
  firstVisibleSkillId?: string | null;
};

export type ResolveWorkflowListDisplayInput = {
  pathname: string;
  requestedMode: CollectionListDisplayMode;
  search?: string | URLSearchParams | null;
  selectedWorkflowId?: string | null;
  firstVisibleWorkflowId?: string | null;
};

const DETAIL_TABS = new Set<string>(WORKFLOW_DETAIL_SUPPORTED_SUBROUTES);

export const COLLECTION_LIST_DISPLAY_MODES = [
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
] as const satisfies readonly CollectionListDisplayModeDefinition[];

export function collectionListDisplayModeByValue(
  value: string,
): CollectionListDisplayModeDefinition | null {
  return COLLECTION_LIST_DISPLAY_MODES.find((mode) => mode.value === value) ?? null;
}

export function isCollectionListDisplayMode(value: string): value is CollectionListDisplayMode {
  return collectionListDisplayModeByValue(value) !== null;
}

export function readWorkflowListDisplayMode(payload: { initialData?: unknown }): CollectionListDisplayMode | undefined {
  const raw = payload.initialData as { workflowListDisplayMode?: unknown } | undefined;
  const value = typeof raw?.workflowListDisplayMode === 'string' ? raw.workflowListDisplayMode : '';
  return isCollectionListDisplayMode(value) ? value : undefined;
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
  const pageSize = params.get('pageSize');
  if (!params.has('limit') && pageSize) {
    params.set('limit', pageSize);
  }
  params.delete('pageSize');
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

function decodeRecurringDetail(pathname: string): { definitionId: string } | null {
  const parts = normalizedPathname(pathname).split('/').slice(1);
  if (parts.length !== 2 || parts[0] !== 'schedules') {
    return null;
  }
  let definitionId: string;
  try {
    definitionId = decodeURIComponent(parts[1] ?? '');
  } catch {
    return null;
  }
  if (!definitionId || definitionId.includes('/') || definitionId.toLowerCase() === 'new') {
    return null;
  }
  return { definitionId };
}

function selectedRecurring(input: ResolveRecurringListDisplayInput): RecurringListSelection {
  const selected = input.selectedDefinitionId?.trim();
  if (selected) {
    return { definitionId: selected, source: 'last-selected' };
  }
  const first = input.firstVisibleDefinitionId?.trim();
  if (first) {
    return { definitionId: first, source: 'first-visible-row' };
  }
  return { definitionId: null, source: 'none' };
}

function encodeRecurringDetailPath(
  definitionId: string,
  search: string | URLSearchParams | null | undefined,
): string {
  return pathWithSearch(`/schedules/${encodeURIComponent(definitionId)}`, search);
}

export function encodeSkillDetailPath(
  skillId: string,
  search?: string | URLSearchParams | null,
): string {
  return pathWithSearch(`/skills/${encodeURIComponent(skillId)}`, search);
}

export function decodeSkillDetail(pathname: string): { skillId: string } | null {
  const parts = normalizedPathname(pathname).split('/').slice(1);
  if (parts.length !== 2 || parts[0] !== 'skills') {
    return null;
  }
  let skillId: string;
  try {
    skillId = decodeURIComponent(parts[1] ?? '');
  } catch {
    return null;
  }
  if (!skillId || skillId.includes('/')) {
    return null;
  }
  return { skillId };
}

function selectedSkill(input: ResolveSkillListDisplayInput): SkillListSelection {
  const selected = input.selectedSkillId?.trim();
  if (selected) {
    return { skillId: selected, source: 'last-selected' };
  }
  const first = input.firstVisibleSkillId?.trim();
  if (first) {
    return { skillId: first, source: 'first-visible-row' };
  }
  return { skillId: null, source: 'none' };
}

export function resolveSkillListDisplay(
  input: ResolveSkillListDisplayInput,
): ResolvedSkillListDisplay | null {
  const pathname = normalizedPathname(input.pathname);
  const requestedMode = input.requestedMode;

  if (pathname === '/skills') {
    if (requestedMode === 'table') {
      return {
        requestedMode,
        effectiveMode: 'table',
        surface: 'skills-table',
        routeAction: 'none',
        primarySurface: 'skill-table',
        listSurface: 'table',
        selection: { skillId: null, source: 'none' },
        targetPath: pathWithSearch('/skills', input.search),
        status: null,
      };
    }

    const selection = selectedSkill(input);
    if (selection.skillId) {
      return {
        requestedMode,
        effectiveMode: requestedMode,
        surface: 'skills-table',
        routeAction: selection.source === 'first-visible-row'
          ? 'resolve-first-row'
          : 'navigate-selected-detail',
        primarySurface: 'skill-detail',
        listSurface: requestedMode === 'hidden' ? 'none' : 'sidebar',
        selection,
        targetPath: encodeSkillDetailPath(selection.skillId, input.search),
        status: null,
      };
    }

    return {
      requestedMode,
      effectiveMode: 'table',
      surface: 'skills-table',
      routeAction: 'none',
      primarySurface: 'empty-skills',
      listSurface: 'table',
      selection,
      targetPath: pathWithSearch('/skills', input.search),
      status: 'No skill can be opened from the current list.',
    };
  }

  const detail = decodeSkillDetail(pathname);
  if (!detail) {
    return null;
  }

  if (requestedMode === 'table') {
    return {
      requestedMode,
      effectiveMode: 'table',
      surface: 'skill-detail',
      routeAction: 'navigate-skills',
      primarySurface: 'skill-table',
      listSurface: 'table',
      selection: { skillId: detail.skillId, source: 'route' },
      targetPath: pathWithSearch('/skills', input.search),
      status: null,
    };
  }

  return {
    requestedMode,
    effectiveMode: requestedMode,
    surface: 'skill-detail',
    routeAction: 'none',
    primarySurface: 'skill-detail',
    listSurface: requestedMode === 'hidden' ? 'none' : 'sidebar',
    selection: { skillId: detail.skillId, source: 'route' },
    targetPath: pathWithSearch(pathname, input.search),
    status: null,
  };
}

export function resolveRecurringListDisplay(
  input: ResolveRecurringListDisplayInput,
): ResolvedRecurringListDisplay | null {
  const pathname = normalizedPathname(input.pathname);
  const requestedMode = input.requestedMode;

  if (pathname === '/schedules') {
    if (requestedMode === 'table') {
      return {
        requestedMode,
        effectiveMode: 'table',
        surface: 'recurring-table',
        routeAction: 'none',
        primarySurface: 'recurring-table',
        listSurface: 'table',
        selection: { definitionId: null, source: 'none' },
        targetPath: pathWithSearch('/schedules', input.search),
        status: null,
      };
    }

    const selection = selectedRecurring(input);
    if (selection.definitionId) {
      return {
        requestedMode,
        effectiveMode: requestedMode,
        surface: 'recurring-table',
        routeAction: selection.source === 'first-visible-row'
          ? 'resolve-first-row'
          : 'navigate-selected-detail',
        primarySurface: 'recurring-detail',
        listSurface: requestedMode === 'hidden' ? 'none' : 'sidebar',
        selection,
        targetPath: encodeRecurringDetailPath(selection.definitionId, input.search),
        status: null,
      };
    }

    return {
      requestedMode,
      effectiveMode: 'table',
      surface: 'recurring-table',
      routeAction: 'none',
      primarySurface: 'empty-recurring',
      listSurface: 'table',
      selection,
      targetPath: pathWithSearch('/schedules', input.search),
      status: 'No recurring schedule can be opened from the current list.',
    };
  }

  const detail = decodeRecurringDetail(pathname);
  if (!detail) {
    return null;
  }

  if (requestedMode === 'table') {
    return {
      requestedMode,
      effectiveMode: 'table',
      surface: 'recurring-detail',
      routeAction: 'navigate-recurring',
      primarySurface: 'recurring-table',
      listSurface: 'table',
      selection: { definitionId: detail.definitionId, source: 'route' },
      targetPath: '/schedules',
      status: null,
    };
  }

  return {
    requestedMode,
    effectiveMode: requestedMode,
    surface: 'recurring-detail',
    routeAction: 'none',
    primarySurface: 'recurring-detail',
    listSurface: requestedMode === 'hidden' ? 'none' : 'sidebar',
    selection: { definitionId: detail.definitionId, source: 'route' },
    targetPath: pathWithSearch(pathname, input.search),
    status: null,
  };
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
          ? 'resolve-first-row'
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
