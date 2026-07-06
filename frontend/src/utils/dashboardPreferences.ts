// MM-964: Local-first dashboard preference layer.
//
// This utility is the single source of truth for operator-tunable dashboard
// preferences (workflow list columns/density, create-page expert mode default,
// workflow detail debug visibility/sidebar layout, and related defaults). Preferences are
// stored locally in `localStorage` under one versioned key and are always read
// back through a validation/sanitization pass so a corrupt, partial, or
// hand-edited blob can never crash the dashboard — invalid values silently fall
// back to the documented defaults.
//
// Scope note: every field below is part of the preference data model and is
// validated and persisted. The first wiring pass connects density, visible
// columns, page size, and live updates (workflow list), the guided/expert
// default (create page), and debug visibility (workflow detail). The remaining
// fields are stored and reset by the same layer so later UI can adopt them
// without a storage migration.

import { DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS } from '../components/PageSizeSelector';

export const DASHBOARD_PREFERENCES_STORAGE_KEY = 'moonmind.dashboard.preferences';
export const DASHBOARD_PREFERENCES_CHANGED_EVENT = 'moonmind:dashboard-preferences-changed';

// Bump only when the stored shape changes in a way the validator cannot
// reconcile from defaults. A mismatched or missing version is treated as an
// invalid blob and reset to defaults rather than migrated.
export const DASHBOARD_PREFERENCES_VERSION = 1;

export type WorkflowListDensity = 'comfortable' | 'compact';

export type WorkflowDetailTab = 'overview' | 'steps' | 'artifacts' | 'runs' | 'debug';

// Columns the operator may hide on the workflow list. The workflow title column
// is the primary anchor and is intentionally not toggleable, so it is excluded
// from this set.
export const TOGGLEABLE_WORKFLOW_LIST_COLUMNS = [
  'status',
  'progress',
  'repository',
  'targetRuntime',
  'updatedAt',
] as const;

export type ToggleableWorkflowListColumn = (typeof TOGGLEABLE_WORKFLOW_LIST_COLUMNS)[number];

export const WORKFLOW_DETAIL_TABS: readonly WorkflowDetailTab[] = [
  'overview',
  'steps',
  'artifacts',
  'runs',
  'debug',
];

// Allowed page sizes reuse the PageSizeSelector options so a persisted value
// can never select an unsupported page size.
export const WORKFLOW_LIST_PAGE_SIZES = PAGE_SIZE_OPTIONS;

export type DashboardPreferences = {
  /** Workflow list row density. */
  workflowListDensity: WorkflowListDensity;
  /** Visibility of each toggleable workflow list column. */
  workflowListColumnVisibility: Record<ToggleableWorkflowListColumn, boolean>;
  /** Default status filter applied on the workflow list. */
  workflowListDefaultStatuses: string[];
  /** Workflow list page size. */
  workflowListPageSize: number;
  /** Whether the workflow list polls for live updates. */
  liveUpdatesEnabled: boolean;
  /** Create page guided (false) vs expert/advanced (true) default. */
  createExpertMode: boolean;
  /** Whether diagnostic debug fields are surfaced on the workflow detail page. */
  debugFieldsVisible: boolean;
  /** Whether the desktop workflow detail sidebar is collapsed on reload. */
  workflowWorkspaceSidebarCollapsed: boolean;
  /** Last workflow explicitly opened by the operator. */
  lastSelectedWorkflowId: string;
  /** Preferred default workflow detail tab. */
  preferredDetailTab: WorkflowDetailTab;
  /** Preferred runtime default for the create page, where safe. */
  defaultRuntime: string;
  /** Preferred provider profile default for the create page, where safe. */
  defaultProviderProfile: string;
  /** Preferred model default for the create page, where safe. */
  defaultModel: string;
};

export const DEFAULT_DASHBOARD_PREFERENCES: DashboardPreferences = {
  workflowListDensity: 'comfortable',
  workflowListColumnVisibility: {
    status: true,
    progress: true,
    repository: true,
    targetRuntime: true,
    updatedAt: true,
  },
  workflowListDefaultStatuses: [],
  workflowListPageSize: DEFAULT_PAGE_SIZE,
  liveUpdatesEnabled: true,
  createExpertMode: false,
  debugFieldsVisible: true,
  workflowWorkspaceSidebarCollapsed: false,
  lastSelectedWorkflowId: '',
  preferredDetailTab: 'overview',
  defaultRuntime: '',
  defaultProviderProfile: '',
  defaultModel: '',
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function sanitizeDensity(value: unknown): WorkflowListDensity {
  return value === 'compact' ? 'compact' : DEFAULT_DASHBOARD_PREFERENCES.workflowListDensity;
}

function sanitizeColumnVisibility(
  value: unknown,
): Record<ToggleableWorkflowListColumn, boolean> {
  const base = { ...DEFAULT_DASHBOARD_PREFERENCES.workflowListColumnVisibility };
  if (!isPlainObject(value)) return base;
  for (const column of TOGGLEABLE_WORKFLOW_LIST_COLUMNS) {
    const candidate = value[column];
    if (typeof candidate === 'boolean') {
      base[column] = candidate;
    }
  }
  return base;
}

function sanitizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  for (const entry of value) {
    if (typeof entry !== 'string') continue;
    const trimmed = entry.trim();
    if (trimmed) seen.add(trimmed);
  }
  return Array.from(seen);
}

function sanitizePageSize(value: unknown): number {
  if (
    typeof value === 'number' &&
    (WORKFLOW_LIST_PAGE_SIZES as readonly number[]).includes(value)
  ) {
    return value;
  }
  return DEFAULT_DASHBOARD_PREFERENCES.workflowListPageSize;
}

function sanitizeBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function sanitizeDetailTab(value: unknown): WorkflowDetailTab {
  return WORKFLOW_DETAIL_TABS.includes(value as WorkflowDetailTab)
    ? (value as WorkflowDetailTab)
    : DEFAULT_DASHBOARD_PREFERENCES.preferredDetailTab;
}

function sanitizeString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

/**
 * Coerce an arbitrary parsed value into a fully-populated, valid preferences
 * object. Unknown, missing, or wrong-typed fields fall back to their defaults
 * field-by-field, so a partial blob keeps whatever valid values it has.
 */
export function sanitizeDashboardPreferences(value: unknown): DashboardPreferences {
  if (!isPlainObject(value)) {
    return { ...DEFAULT_DASHBOARD_PREFERENCES };
  }
  return {
    workflowListDensity: sanitizeDensity(value.workflowListDensity),
    workflowListColumnVisibility: sanitizeColumnVisibility(value.workflowListColumnVisibility),
    workflowListDefaultStatuses: sanitizeStringList(value.workflowListDefaultStatuses),
    workflowListPageSize: sanitizePageSize(value.workflowListPageSize),
    liveUpdatesEnabled: sanitizeBoolean(
      value.liveUpdatesEnabled,
      DEFAULT_DASHBOARD_PREFERENCES.liveUpdatesEnabled,
    ),
    createExpertMode: sanitizeBoolean(
      value.createExpertMode,
      DEFAULT_DASHBOARD_PREFERENCES.createExpertMode,
    ),
    debugFieldsVisible: sanitizeBoolean(
      value.debugFieldsVisible,
      DEFAULT_DASHBOARD_PREFERENCES.debugFieldsVisible,
    ),
    workflowWorkspaceSidebarCollapsed: sanitizeBoolean(
      value.workflowWorkspaceSidebarCollapsed,
      DEFAULT_DASHBOARD_PREFERENCES.workflowWorkspaceSidebarCollapsed,
    ),
    lastSelectedWorkflowId: sanitizeString(value.lastSelectedWorkflowId),
    preferredDetailTab: sanitizeDetailTab(value.preferredDetailTab),
    defaultRuntime: sanitizeString(value.defaultRuntime),
    defaultProviderProfile: sanitizeString(value.defaultProviderProfile),
    defaultModel: sanitizeString(value.defaultModel),
  };
}

type StoredEnvelope = {
  version: number;
  preferences: unknown;
};

/**
 * Read the persisted dashboard preferences. Any failure — unavailable storage,
 * unparseable JSON, a version mismatch, or an invalid shape — resolves to the
 * defaults instead of throwing.
 */
export function readDashboardPreferences(): DashboardPreferences {
  try {
    const raw = window.localStorage.getItem(DASHBOARD_PREFERENCES_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_DASHBOARD_PREFERENCES };
    const parsed = JSON.parse(raw) as unknown;
    if (!isPlainObject(parsed)) return { ...DEFAULT_DASHBOARD_PREFERENCES };
    const envelope = parsed as Partial<StoredEnvelope>;
    if (envelope.version !== DASHBOARD_PREFERENCES_VERSION) {
      return { ...DEFAULT_DASHBOARD_PREFERENCES };
    }
    return sanitizeDashboardPreferences(envelope.preferences);
  } catch {
    return { ...DEFAULT_DASHBOARD_PREFERENCES };
  }
}

/**
 * Persist a complete set of dashboard preferences. The value is sanitized
 * before writing so callers cannot store an invalid blob. Storage failures are
 * swallowed to keep preferences best-effort.
 */
export function writeDashboardPreferences(
  preferences: DashboardPreferences,
): DashboardPreferences {
  const sanitized = sanitizeDashboardPreferences(preferences);
  try {
    const envelope: StoredEnvelope = {
      version: DASHBOARD_PREFERENCES_VERSION,
      preferences: sanitized,
    };
    window.localStorage.setItem(
      DASHBOARD_PREFERENCES_STORAGE_KEY,
      JSON.stringify(envelope),
    );
    window.dispatchEvent(new Event(DASHBOARD_PREFERENCES_CHANGED_EVENT));
  } catch {
    // Keep dashboard preferences best-effort when storage is unavailable.
  }
  return sanitized;
}

/**
 * Apply a partial patch on top of the currently stored preferences and persist
 * the result. Returns the new, sanitized preferences.
 */
export function updateDashboardPreferences(
  patch: Partial<DashboardPreferences>,
): DashboardPreferences {
  const current = readDashboardPreferences();
  return writeDashboardPreferences({ ...current, ...patch });
}

/**
 * Reset all dashboard preferences to their defaults by removing the stored
 * blob. Returns the default preferences.
 */
export function resetDashboardPreferences(): DashboardPreferences {
  try {
    window.localStorage.removeItem(DASHBOARD_PREFERENCES_STORAGE_KEY);
  } catch {
    // Best-effort reset; defaults are returned regardless.
  }
  window.dispatchEvent(new Event(DASHBOARD_PREFERENCES_CHANGED_EVENT));
  return { ...DEFAULT_DASHBOARD_PREFERENCES };
}
