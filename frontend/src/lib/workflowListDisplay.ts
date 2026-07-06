import type { ReactNode } from 'react';
import { PanelLeft, Rows3, Square, type LucideIcon } from 'lucide-react';

import type { WorkflowListDisplayMode } from '../utils/dashboardPreferences';

export type WorkflowListDisplaySurface =
  | 'workflows-table'
  | 'workflow-detail'
  | 'workflow-start';

export type WorkflowListRouteAction =
  | 'none'
  | 'navigate-workflows'
  | 'navigate-selected-detail'
  | 'resolve-first-workflow';

export type WorkflowListPrimarySurface =
  | 'workflow-detail'
  | 'workflow-start'
  | 'workflow-table'
  | 'empty-workflows';

export type WorkflowListSurfaceRegion = 'none' | 'sidebar' | 'table';

export type WorkflowListDisplaySurfaceContract = {
  surface: WorkflowListDisplaySurface;
  supportsHidden: boolean;
  supportsSidebar: boolean;
  supportsTable: boolean;
  hiddenPrimarySurface?: ReactNode;
  sidebarPrimarySurface?: ReactNode;
  onTableMode: 'navigate-workflows' | 'render-local-table' | 'unsupported';
};

export type ListDisplayModeDefinition = {
  value: WorkflowListDisplayMode;
  label: string;
  icon: LucideIcon;
  listRegion: 'none' | 'sidebar' | 'primary-surface';
};

export type WorkflowListDisplayResolutionOptions = {
  hasRememberedSelection?: boolean;
  hasFirstVisibleWorkflow?: boolean;
};

export type ResolvedWorkflowListDisplay = {
  requestedMode: WorkflowListDisplayMode;
  effectiveMode: WorkflowListDisplayMode;
  surface: WorkflowListDisplaySurface;
  routeAction: WorkflowListRouteAction;
  primarySurface: WorkflowListPrimarySurface;
  listSurface: WorkflowListSurfaceRegion;
};

export const WORKFLOW_LIST_DISPLAY_MODES = [
  {
    value: 'hidden',
    label: 'No list',
    icon: Square,
    listRegion: 'none',
  },
  {
    value: 'sidebar',
    label: 'Sidebar list',
    icon: PanelLeft,
    listRegion: 'sidebar',
  },
  {
    value: 'table',
    label: 'Full screen table',
    icon: Rows3,
    listRegion: 'primary-surface',
  },
] as const satisfies readonly ListDisplayModeDefinition[];

export const WORKFLOW_LIST_DISPLAY_SURFACE_CONTRACTS = {
  'workflows-table': {
    surface: 'workflows-table',
    supportsHidden: true,
    supportsSidebar: true,
    supportsTable: true,
    onTableMode: 'render-local-table',
  },
  'workflow-detail': {
    surface: 'workflow-detail',
    supportsHidden: true,
    supportsSidebar: true,
    supportsTable: true,
    onTableMode: 'navigate-workflows',
  },
  'workflow-start': {
    surface: 'workflow-start',
    supportsHidden: true,
    supportsSidebar: true,
    supportsTable: true,
    onTableMode: 'navigate-workflows',
  },
} as const satisfies Record<WorkflowListDisplaySurface, WorkflowListDisplaySurfaceContract>;

function decodePathSegment(segment: string): string | null {
  try {
    const decoded = decodeURIComponent(segment);
    return decoded.includes('/') ? null : decoded;
  } catch {
    return null;
  }
}

function isWorkflowDetailSegment(segment: string): boolean {
  const decoded = decodePathSegment(segment);
  return Boolean(decoded && /^[A-Za-z0-9][A-Za-z0-9._:{}-]{0,254}$/.test(decoded));
}

export function surfaceForWorkflowPath(pathname: string): WorkflowListDisplaySurface | null {
  const normalized = pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
  if (normalized === '/workflows') return 'workflows-table';
  if (normalized === '/workflows/new') return 'workflow-start';
  const parts = normalized.split('/').slice(1);
  if (
    parts[0] === 'workflows' &&
    (parts.length === 2 || parts.length === 3) &&
    parts[1] !== 'new' &&
    isWorkflowDetailSegment(parts[1] ?? '') &&
    (parts.length === 2 || ['steps', 'artifacts', 'runs', 'debug'].includes(parts[2] ?? ''))
  ) {
    return 'workflow-detail';
  }
  return null;
}

export function contractForWorkflowPath(pathname: string): WorkflowListDisplaySurfaceContract | null {
  const surface = surfaceForWorkflowPath(pathname);
  return surface ? WORKFLOW_LIST_DISPLAY_SURFACE_CONTRACTS[surface] : null;
}

export function isWorkflowListDisplayModeSupported(
  contract: WorkflowListDisplaySurfaceContract,
  mode: WorkflowListDisplayMode,
): boolean {
  if (mode === 'hidden') return contract.supportsHidden;
  if (mode === 'sidebar') return contract.supportsSidebar;
  return contract.supportsTable;
}

export function effectiveWorkflowListDisplayMode(
  pathname: string,
  preferredMode: WorkflowListDisplayMode,
): WorkflowListDisplayMode | null {
  const contract = contractForWorkflowPath(pathname);
  if (!contract) return null;
  if (contract.surface === 'workflows-table') return 'table';
  if (isWorkflowListDisplayModeSupported(contract, preferredMode) && preferredMode !== 'table') {
    return preferredMode;
  }
  return contract.surface === 'workflow-start' ? 'hidden' : 'sidebar';
}

function listSurfaceForMode(mode: WorkflowListDisplayMode): WorkflowListSurfaceRegion {
  if (mode === 'hidden') return 'none';
  if (mode === 'sidebar') return 'sidebar';
  return 'table';
}

export function resolveWorkflowListDisplay(
  pathname: string,
  requestedMode: WorkflowListDisplayMode,
  options: WorkflowListDisplayResolutionOptions = {},
): ResolvedWorkflowListDisplay | null {
  const contract = contractForWorkflowPath(pathname);
  if (!contract || !isWorkflowListDisplayModeSupported(contract, requestedMode)) return null;

  if (contract.surface === 'workflows-table') {
    if (requestedMode === 'table') {
      return {
        requestedMode,
        effectiveMode: 'table',
        surface: contract.surface,
        routeAction: 'none',
        primarySurface: 'workflow-table',
        listSurface: 'table',
      };
    }
    if (options.hasRememberedSelection) {
      return {
        requestedMode,
        effectiveMode: requestedMode,
        surface: contract.surface,
        routeAction: 'navigate-selected-detail',
        primarySurface: 'workflow-detail',
        listSurface: listSurfaceForMode(requestedMode),
      };
    }
    if (options.hasFirstVisibleWorkflow) {
      return {
        requestedMode,
        effectiveMode: requestedMode,
        surface: contract.surface,
        routeAction: 'resolve-first-workflow',
        primarySurface: 'workflow-detail',
        listSurface: listSurfaceForMode(requestedMode),
      };
    }
    return {
      requestedMode,
      effectiveMode: 'table',
      surface: contract.surface,
      routeAction: 'none',
      primarySurface: 'empty-workflows',
      listSurface: 'table',
    };
  }

  if (requestedMode === 'table') {
    return {
      requestedMode,
      effectiveMode: 'table',
      surface: contract.surface,
      routeAction: 'navigate-workflows',
      primarySurface: 'workflow-table',
      listSurface: 'table',
    };
  }

  return {
    requestedMode,
    effectiveMode: requestedMode,
    surface: contract.surface,
    routeAction: 'none',
    primarySurface: contract.surface === 'workflow-start' ? 'workflow-start' : 'workflow-detail',
    listSurface: listSurfaceForMode(requestedMode),
  };
}
