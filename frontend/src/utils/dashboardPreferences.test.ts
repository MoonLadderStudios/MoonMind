import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  DASHBOARD_PREFERENCES_STORAGE_KEY,
  DASHBOARD_PREFERENCES_VERSION,
  DEFAULT_DASHBOARD_PREFERENCES,
  readDashboardPreferences,
  resetDashboardPreferences,
  sanitizeDashboardPreferences,
  updateDashboardPreferences,
  writeDashboardPreferences,
} from './dashboardPreferences';

function storedRaw(): string | null {
  return window.localStorage.getItem(DASHBOARD_PREFERENCES_STORAGE_KEY);
}

describe('dashboardPreferences', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  describe('read/write (MM-964 preference read/write)', () => {
    it('returns defaults when nothing is stored', () => {
      expect(readDashboardPreferences()).toEqual(DEFAULT_DASHBOARD_PREFERENCES);
    });

    it('round-trips a full preference object across a simulated reload', () => {
      const next = writeDashboardPreferences({
        ...DEFAULT_DASHBOARD_PREFERENCES,
        workflowListDensity: 'compact',
        workflowListColumnVisibility: {
          status: true,
          progress: false,
          repository: true,
          targetRuntime: false,
          updatedAt: true,
        },
        workflowListPageSize: 100,
        liveUpdatesEnabled: false,
        createExpertMode: true,
        debugFieldsVisible: false,
        workflowWorkspaceSidebarCollapsed: true,
        preferredDetailTab: 'steps',
        lastSelectedWorkflowId: 'workflow-123',
        defaultRuntime: 'codex_cli',
      });

      // A fresh read simulates a page reload reading from localStorage.
      const reloaded = readDashboardPreferences();
      expect(reloaded).toEqual(next);
      expect(reloaded.workflowListDensity).toBe('compact');
      expect(reloaded.workflowListColumnVisibility.progress).toBe(false);
      expect(reloaded.createExpertMode).toBe(true);
      expect(reloaded.debugFieldsVisible).toBe(false);
      expect(reloaded.workflowWorkspaceSidebarCollapsed).toBe(true);
      expect(reloaded.lastSelectedWorkflowId).toBe('workflow-123');
      expect(reloaded.preferredDetailTab).toBe('steps');
    });

    it('persists with a version envelope', () => {
      writeDashboardPreferences({ ...DEFAULT_DASHBOARD_PREFERENCES, createExpertMode: true });
      const parsed = JSON.parse(storedRaw() ?? '{}');
      expect(parsed.version).toBe(DASHBOARD_PREFERENCES_VERSION);
      expect(parsed.preferences.createExpertMode).toBe(true);
    });

    it('applies a partial patch on top of stored preferences', () => {
      writeDashboardPreferences({ ...DEFAULT_DASHBOARD_PREFERENCES, workflowListDensity: 'compact' });
      const updated = updateDashboardPreferences({ createExpertMode: true });
      expect(updated.workflowListDensity).toBe('compact');
      expect(updated.createExpertMode).toBe(true);
      // The patch is durable.
      expect(readDashboardPreferences()).toEqual(updated);
    });
  });

  describe('invalid preference fallback (MM-964 invalid preference fallback)', () => {
    it('falls back to defaults when the stored blob is not valid JSON', () => {
      window.localStorage.setItem(DASHBOARD_PREFERENCES_STORAGE_KEY, '{not json');
      expect(readDashboardPreferences()).toEqual(DEFAULT_DASHBOARD_PREFERENCES);
    });

    it('falls back to defaults when the stored version does not match', () => {
      window.localStorage.setItem(
        DASHBOARD_PREFERENCES_STORAGE_KEY,
        JSON.stringify({
          version: DASHBOARD_PREFERENCES_VERSION + 1,
          preferences: { workflowListDensity: 'compact' },
        }),
      );
      expect(readDashboardPreferences()).toEqual(DEFAULT_DASHBOARD_PREFERENCES);
    });

    it('falls back to defaults when the stored value is not an object', () => {
      window.localStorage.setItem(DASHBOARD_PREFERENCES_STORAGE_KEY, JSON.stringify([1, 2, 3]));
      expect(readDashboardPreferences()).toEqual(DEFAULT_DASHBOARD_PREFERENCES);
    });

    it('ignores individual invalid fields while keeping valid ones', () => {
      window.localStorage.setItem(
        DASHBOARD_PREFERENCES_STORAGE_KEY,
        JSON.stringify({
          version: DASHBOARD_PREFERENCES_VERSION,
          preferences: {
            workflowListDensity: 'ultra-compact', // invalid enum
            workflowListPageSize: 7, // unsupported page size
            preferredDetailTab: 'nope', // invalid enum
            liveUpdatesEnabled: 'yes', // wrong type
            workflowWorkspaceSidebarCollapsed: 'yes', // wrong type
            lastSelectedWorkflowId: '  workflow-456  ',
            createExpertMode: true, // valid
            workflowListColumnVisibility: { repository: false, bogus: true },
            workflowListDefaultStatuses: ['executing', 42, '  failed  ', ''],
            defaultRuntime: '  codex_cli  ',
          },
        }),
      );
      const prefs = readDashboardPreferences();
      expect(prefs.workflowListDensity).toBe('comfortable'); // reset
      expect(prefs.workflowListPageSize).toBe(DEFAULT_DASHBOARD_PREFERENCES.workflowListPageSize);
      expect(prefs.preferredDetailTab).toBe('overview'); // reset
      expect(prefs.liveUpdatesEnabled).toBe(true); // reset to default
      expect(prefs.workflowWorkspaceSidebarCollapsed).toBe(false); // reset to default
      expect(prefs.lastSelectedWorkflowId).toBe('workflow-456'); // trimmed
      expect(prefs.createExpertMode).toBe(true); // kept
      expect(prefs.workflowListColumnVisibility.repository).toBe(false); // kept
      expect(prefs.workflowListColumnVisibility).not.toHaveProperty('bogus'); // dropped
      expect(prefs.workflowListDefaultStatuses).toEqual(['executing', 'failed']); // sanitized
      expect(prefs.defaultRuntime).toBe('codex_cli'); // trimmed
    });

    it('sanitizeDashboardPreferences returns defaults for non-object input', () => {
      expect(sanitizeDashboardPreferences(null)).toEqual(DEFAULT_DASHBOARD_PREFERENCES);
      expect(sanitizeDashboardPreferences('nope')).toEqual(DEFAULT_DASHBOARD_PREFERENCES);
      expect(sanitizeDashboardPreferences(123)).toEqual(DEFAULT_DASHBOARD_PREFERENCES);
    });

    it('never persists an invalid value through write', () => {
      const written = writeDashboardPreferences({
        ...DEFAULT_DASHBOARD_PREFERENCES,
        // Force-cast to exercise the sanitizer on the write path.
        workflowListDensity: 'bogus' as never,
        workflowListPageSize: 9999 as never,
      });
      expect(written.workflowListDensity).toBe('comfortable');
      expect(written.workflowListPageSize).toBe(DEFAULT_DASHBOARD_PREFERENCES.workflowListPageSize);
      expect(readDashboardPreferences().workflowListDensity).toBe('comfortable');
    });

    it('defaults include progress and do not include removed nextAction column', () => {
      expect(DEFAULT_DASHBOARD_PREFERENCES.workflowListColumnVisibility.progress).toBe(true);
      expect(DEFAULT_DASHBOARD_PREFERENCES.workflowListColumnVisibility).not.toHaveProperty(
        'nextAction',
      );
    });

    it('ignores legacy nextAction visibility during sanitization', () => {
      const prefs = sanitizeDashboardPreferences({
        workflowListColumnVisibility: {
          status: false,
          nextAction: true,
          progress: false,
        },
      });

      expect(prefs.workflowListColumnVisibility.status).toBe(false);
      expect(prefs.workflowListColumnVisibility.progress).toBe(false);
      expect(prefs.workflowListColumnVisibility).not.toHaveProperty('nextAction');
    });

    it('round-trip preferences no longer include nextAction', () => {
      writeDashboardPreferences({
        ...DEFAULT_DASHBOARD_PREFERENCES,
        workflowListColumnVisibility: {
          ...DEFAULT_DASHBOARD_PREFERENCES.workflowListColumnVisibility,
          progress: false,
        },
      });

      const parsed = JSON.parse(storedRaw() ?? '{}');
      expect(parsed.preferences.workflowListColumnVisibility.progress).toBe(false);
      expect(parsed.preferences.workflowListColumnVisibility).not.toHaveProperty('nextAction');
    });

    it('MM-1000 keeps a valid persisted workflow workspace sidebar collapse preference', () => {
      const prefs = sanitizeDashboardPreferences({
        workflowWorkspaceSidebarCollapsed: true,
      });

      expect(prefs.workflowWorkspaceSidebarCollapsed).toBe(true);
    });

    it('MM-1113 keeps a valid last selected workflow preference', () => {
      const prefs = sanitizeDashboardPreferences({
        lastSelectedWorkflowId: '  remembered-workflow  ',
      });

      expect(prefs.lastSelectedWorkflowId).toBe('remembered-workflow');
    });

    it('MM-1113 sanitizes invalid last selected workflow values to the default', () => {
      for (const candidate of [null, undefined, 42, false, {}, []]) {
        expect(sanitizeDashboardPreferences({ lastSelectedWorkflowId: candidate }).lastSelectedWorkflowId).toBe('');
      }
    });

    it('MM-1145 defaults the recurring list display mode to table and the remembered definition to empty', () => {
      expect(DEFAULT_DASHBOARD_PREFERENCES.recurringListDisplayMode).toBe('table');
      expect(DEFAULT_DASHBOARD_PREFERENCES.lastSelectedDefinitionId).toBe('');
    });

    it('MM-1145 keeps a valid persisted recurring list display mode', () => {
      for (const mode of ['hidden', 'sidebar', 'table'] as const) {
        expect(
          sanitizeDashboardPreferences({ recurringListDisplayMode: mode }).recurringListDisplayMode,
        ).toBe(mode);
      }
    });

    it('MM-1145 sanitizes an invalid recurring list display mode back to table', () => {
      for (const candidate of ['collapsed', '', 42, null, undefined, {}, []]) {
        expect(
          sanitizeDashboardPreferences({ recurringListDisplayMode: candidate }).recurringListDisplayMode,
        ).toBe('table');
      }
    });

    it('MM-1145 trims the remembered recurring definition and rejects non-string values', () => {
      expect(
        sanitizeDashboardPreferences({ lastSelectedDefinitionId: '  schedule-one  ' }).lastSelectedDefinitionId,
      ).toBe('schedule-one');
      for (const candidate of [null, undefined, 42, false, {}, []]) {
        expect(
          sanitizeDashboardPreferences({ lastSelectedDefinitionId: candidate }).lastSelectedDefinitionId,
        ).toBe('');
      }
    });

    it('MM-1145 round-trips the recurring mode and remembered definition across a reload', () => {
      writeDashboardPreferences({
        ...DEFAULT_DASHBOARD_PREFERENCES,
        recurringListDisplayMode: 'hidden',
        lastSelectedDefinitionId: 'schedule-two',
      });

      const reloaded = readDashboardPreferences();
      expect(reloaded.recurringListDisplayMode).toBe('hidden');
      expect(reloaded.lastSelectedDefinitionId).toBe('schedule-two');
    });
  });

  describe('reset behavior (MM-964 reset behavior)', () => {
    it('clears stored preferences and returns defaults', () => {
      writeDashboardPreferences({
        ...DEFAULT_DASHBOARD_PREFERENCES,
        workflowListDensity: 'compact',
        createExpertMode: true,
        lastSelectedWorkflowId: 'remembered-workflow',
      });
      expect(storedRaw()).not.toBeNull();

      const reset = resetDashboardPreferences();
      expect(reset).toEqual(DEFAULT_DASHBOARD_PREFERENCES);
      expect(reset.lastSelectedWorkflowId).toBe('');
      expect(storedRaw()).toBeNull();
      // A subsequent read also yields defaults.
      expect(readDashboardPreferences()).toEqual(DEFAULT_DASHBOARD_PREFERENCES);
    });
  });
});
