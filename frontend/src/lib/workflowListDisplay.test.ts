import { describe, expect, it } from 'vitest';

import {
  WORKFLOW_LIST_DISPLAY_MODES,
  contractForWorkflowPath,
  effectiveWorkflowListDisplayMode,
  isWorkflowListDisplayModeSupported,
  resolveWorkflowListDisplay,
} from './workflowListDisplay';

describe('workflow list display contract', () => {
  it('declares the canonical hidden, sidebar, and table modes with labels and regions', () => {
    expect(WORKFLOW_LIST_DISPLAY_MODES.map((mode) => [mode.value, mode.label, mode.listRegion])).toEqual([
      ['hidden', 'No list', 'none'],
      ['sidebar', 'Sidebar list', 'sidebar'],
      ['table', 'Full screen table', 'primary-surface'],
    ]);
  });

  it('requires Workflows and Create surfaces to opt into supported modes', () => {
    for (const path of ['/workflows', '/workflows/test-123', '/workflows/test-123/steps', '/workflows/new']) {
      const contract = contractForWorkflowPath(path);
      expect(contract).not.toBeNull();
      expect(isWorkflowListDisplayModeSupported(contract!, 'hidden')).toBe(true);
      expect(isWorkflowListDisplayModeSupported(contract!, 'sidebar')).toBe(true);
      expect(isWorkflowListDisplayModeSupported(contract!, 'table')).toBe(true);
    }
  });

  it('does not expose the workflow display contract on unrelated dashboard routes', () => {
    expect(contractForWorkflowPath('/settings')).toBeNull();
    expect(contractForWorkflowPath('/skills')).toBeNull();
    expect(contractForWorkflowPath('/schedules')).toBeNull();
  });

  it('keeps the Workflows table as the canonical effective table mode', () => {
    expect(effectiveWorkflowListDisplayMode('/workflows', 'hidden')).toBe('table');
    expect(effectiveWorkflowListDisplayMode('/workflows', 'sidebar')).toBe('table');
    expect(effectiveWorkflowListDisplayMode('/workflows/test-123', 'table')).toBe('sidebar');
    expect(effectiveWorkflowListDisplayMode('/workflows/new', 'table')).toBe('hidden');
  });

  it('declares table, detail, and create composition without enabling undeclared routes', () => {
    expect(resolveWorkflowListDisplay('/workflows', 'table')).toMatchObject({
      effectiveMode: 'table',
      surface: 'workflows-table',
      routeAction: 'none',
      primarySurface: 'workflow-table',
      listSurface: 'table',
    });
    expect(resolveWorkflowListDisplay('/workflows/test-123/steps', 'hidden')).toMatchObject({
      effectiveMode: 'hidden',
      surface: 'workflow-detail',
      routeAction: 'none',
      primarySurface: 'workflow-detail',
      listSurface: 'none',
    });
    expect(resolveWorkflowListDisplay('/workflows/new', 'sidebar')).toMatchObject({
      effectiveMode: 'sidebar',
      surface: 'workflow-start',
      routeAction: 'none',
      primarySurface: 'workflow-start',
      listSurface: 'sidebar',
    });
    expect(resolveWorkflowListDisplay('/settings', 'sidebar')).toBeNull();
    expect(resolveWorkflowListDisplay('/workflows/%2Fsecret', 'sidebar')).toBeNull();
  });

  it('resolves Workflows table hidden/sidebar requests through remembered or first-row selection', () => {
    expect(
      resolveWorkflowListDisplay('/workflows', 'hidden', {
        hasRememberedSelection: true,
        hasFirstVisibleWorkflow: true,
      }),
    ).toMatchObject({
      effectiveMode: 'hidden',
      routeAction: 'navigate-selected-detail',
      primarySurface: 'workflow-detail',
      listSurface: 'none',
    });
    expect(
      resolveWorkflowListDisplay('/workflows', 'sidebar', {
        hasRememberedSelection: false,
        hasFirstVisibleWorkflow: true,
      }),
    ).toMatchObject({
      effectiveMode: 'sidebar',
      routeAction: 'resolve-first-workflow',
      primarySurface: 'workflow-detail',
      listSurface: 'sidebar',
    });
    expect(
      resolveWorkflowListDisplay('/workflows', 'hidden', {
        hasRememberedSelection: false,
        hasFirstVisibleWorkflow: false,
      }),
    ).toMatchObject({
      effectiveMode: 'table',
      routeAction: 'none',
      primarySurface: 'empty-workflows',
      listSurface: 'table',
    });
  });

  it('honors surface declarations when a future page opts out of a mode', () => {
    expect(
      isWorkflowListDisplayModeSupported(
        {
          surface: 'workflow-detail',
          supportsHidden: true,
          supportsSidebar: false,
          supportsTable: true,
          onTableMode: 'navigate-workflows',
        },
        'sidebar',
      ),
    ).toBe(false);
  });
});
