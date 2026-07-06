import type { ReactNode } from 'react';
import { PanelLeft, Rows3, Square, type LucideIcon } from 'lucide-react';

import type { WorkflowListDisplayMode } from '../utils/dashboardPreferences';

export type WorkflowListDisplaySurface =
  | 'workflows-table'
  | 'workflow-detail'
  | 'workflow-start';

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

export function surfaceForWorkflowPath(pathname: string): WorkflowListDisplaySurface | null {
  const normalized = pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
  if (normalized === '/workflows') return 'workflows-table';
  if (normalized === '/workflows/new') return 'workflow-start';
  if (/^\/workflows\/[^/]+(?:\/(?:steps|artifacts|runs|debug))?$/.test(normalized)) {
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
