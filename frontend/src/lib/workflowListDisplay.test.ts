import { describe, expect, it } from 'vitest';

import {
  WORKFLOW_LIST_DISPLAY_MODES,
  contractForWorkflowPath,
  effectiveWorkflowListDisplayMode,
  isWorkflowListDisplayModeSupported,
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
});
