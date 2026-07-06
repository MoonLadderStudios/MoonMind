import { describe, expect, it } from 'vitest';

import {
  WORKFLOW_LIST_DISPLAY_MODES,
  resolveWorkflowListDisplay,
  workflowListDisplayModeByValue,
  type WorkflowListDisplayMode,
} from './workflowListDisplayMode';

const modeValues = (): WorkflowListDisplayMode[] => WORKFLOW_LIST_DISPLAY_MODES.map((mode) => mode.value);

describe('workflow list display mode registry', () => {
  it('exposes exactly the canonical hidden, sidebar, and table modes', () => {
    expect(modeValues()).toEqual(['hidden', 'sidebar', 'table']);
    expect(WORKFLOW_LIST_DISPLAY_MODES).toHaveLength(3);
  });

  it('uses canonical labels, icon identities, and list regions without label matching', () => {
    expect(WORKFLOW_LIST_DISPLAY_MODES).toEqual([
      { value: 'hidden', label: 'No list', icon: 'Square', listRegion: 'none' },
      { value: 'sidebar', label: 'Sidebar list', icon: 'PanelLeft', listRegion: 'sidebar' },
      { value: 'table', label: 'Full screen table', icon: 'Rows3', listRegion: 'primary-surface' },
    ]);
    expect(workflowListDisplayModeByValue('hidden')?.label).toBe('No list');
    expect(workflowListDisplayModeByValue('No list')).toBeNull();
  });
});

describe('resolveWorkflowListDisplay', () => {
  it('returns the full declarative shape for the workflows table route', () => {
    expect(resolveWorkflowListDisplay({ pathname: '/workflows', requestedMode: 'table' })).toEqual({
      requestedMode: 'table',
      effectiveMode: 'table',
      surface: 'workflows-table',
      routeAction: 'none',
      primarySurface: 'workflow-table',
      listSurface: 'table',
      selection: { workflowId: null, source: 'none' },
      targetPath: '/workflows',
      status: null,
    });
  });

  it('opens the selected workflow from /workflows for hidden and sidebar modes', () => {
    for (const requestedMode of ['hidden', 'sidebar'] as const) {
      expect(resolveWorkflowListDisplay({
        pathname: '/workflows',
        requestedMode,
        selectedWorkflowId: 'mm:selected',
        search: '?source=temporal',
      })).toMatchObject({
        requestedMode,
        effectiveMode: requestedMode,
        surface: 'workflows-table',
        routeAction: 'navigate-selected-detail',
        primarySurface: 'workflow-detail',
        listSurface: requestedMode === 'hidden' ? 'none' : 'sidebar',
        selection: { workflowId: 'mm:selected', source: 'last-selected' },
        targetPath: '/workflows/mm%3Aselected?source=temporal',
      });
    }
  });

  it('uses the first visible workflow from /workflows when no selected workflow exists', () => {
    expect(resolveWorkflowListDisplay({
      pathname: '/workflows',
      requestedMode: 'sidebar',
      firstVisibleWorkflowId: 'mm:first-visible',
    })).toMatchObject({
      effectiveMode: 'sidebar',
      routeAction: 'resolve-first-workflow',
      primarySurface: 'workflow-detail',
      listSurface: 'sidebar',
      selection: { workflowId: 'mm:first-visible', source: 'first-visible-row' },
      targetPath: '/workflows/mm%3Afirst-visible',
    });
  });

  it('keeps /workflows effective as table when no workflow can be opened', () => {
    expect(resolveWorkflowListDisplay({ pathname: '/workflows', requestedMode: 'hidden' })).toEqual({
      requestedMode: 'hidden',
      effectiveMode: 'table',
      surface: 'workflows-table',
      routeAction: 'none',
      primarySurface: 'empty-workflows',
      listSurface: 'table',
      selection: { workflowId: null, source: 'none' },
      targetPath: '/workflows',
      status: 'No workflow can be opened from the current list.',
    });
  });

  it('preserves detail subroutes when switching only between hidden and sidebar', () => {
    expect(resolveWorkflowListDisplay({
      pathname: '/workflows/mm%3A123/steps',
      search: '?source=temporal',
      requestedMode: 'hidden',
    })).toMatchObject({
      effectiveMode: 'hidden',
      surface: 'workflow-detail',
      routeAction: 'none',
      primarySurface: 'workflow-detail',
      listSurface: 'none',
      selection: { workflowId: 'mm:123', source: 'route' },
      targetPath: '/workflows/mm%3A123/steps?source=temporal',
    });

    expect(resolveWorkflowListDisplay({
      pathname: '/workflows/mm%3A123/steps',
      requestedMode: 'sidebar',
    })).toMatchObject({
      effectiveMode: 'sidebar',
      routeAction: 'none',
      targetPath: '/workflows/mm%3A123/steps',
    });
  });

  it('navigates detail routes and create to the workflows table for table mode', () => {
    expect(resolveWorkflowListDisplay({
      pathname: '/workflows/mm%3A123/debug',
      search: '?source=temporal&limit=10&token=secret&unsafe=1',
      requestedMode: 'table',
    })).toMatchObject({
      effectiveMode: 'table',
      surface: 'workflow-detail',
      routeAction: 'navigate-workflows',
      primarySurface: 'workflow-table',
      listSurface: 'table',
      targetPath: '/workflows?source=temporal&limit=10',
    });

    expect(resolveWorkflowListDisplay({
      pathname: '/workflows/new',
      requestedMode: 'table',
    })).toMatchObject({
      effectiveMode: 'table',
      surface: 'workflow-start',
      routeAction: 'navigate-workflows',
      primarySurface: 'workflow-table',
      listSurface: 'table',
      targetPath: '/workflows',
    });
  });

  it('keeps create as primary and renders the workflow list as a sidebar in sidebar mode', () => {
    expect(resolveWorkflowListDisplay({ pathname: '/workflows/new', requestedMode: 'sidebar' })).toMatchObject({
      requestedMode: 'sidebar',
      effectiveMode: 'sidebar',
      surface: 'workflow-start',
      routeAction: 'none',
      primarySurface: 'workflow-start',
      listSurface: 'sidebar',
      targetPath: '/workflows/new',
      status: null,
    });
    expect(resolveWorkflowListDisplay({ pathname: '/workflows/new', requestedMode: 'hidden' })).toMatchObject({
      requestedMode: 'hidden',
      effectiveMode: 'hidden',
      surface: 'workflow-start',
      routeAction: 'none',
      primarySurface: 'workflow-start',
      listSurface: 'none',
      targetPath: '/workflows/new',
      status: null,
    });
  });

  it('returns null for unsupported dashboard routes', () => {
    expect(resolveWorkflowListDisplay({ pathname: '/settings', requestedMode: 'table' })).toBeNull();
  });
});
