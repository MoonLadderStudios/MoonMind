import { describe, expect, it } from 'vitest';

import {
  EXECUTING_STATUS_PILL_TRACEABILITY,
  executionStatusPillProps,
} from './executionStatusPillClasses';

describe('executionStatusPillProps', () => {
  it('adds shimmer selector metadata for executing and planning status pills', () => {
    expect(executionStatusPillProps('executing')).toMatchObject({
      className: 'status status-running is-executing',
      'data-state': 'executing',
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': 'executing',
    });

    expect(executionStatusPillProps('planning')).toMatchObject({
      className: 'status status-running is-planning',
      'data-state': 'planning',
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': 'planning',
    });

    expect(executionStatusPillProps('running')).toEqual({
      className: 'status status-running',
    });
    expect(executionStatusPillProps('waiting')).toEqual({
      className: 'status status-waiting',
    });
  });

  it('keeps the existing class helper output for non-executing states across the MM-491 state matrix', () => {
    expect(executionStatusPillProps('completed')).toEqual({ className: 'status status-completed' });
    expect(executionStatusPillProps('failed')).toEqual({ className: 'status status-failed' });
    expect(executionStatusPillProps('executing').className).toBe('status status-running is-executing');
    expect(executionStatusPillProps('planning').className).toBe('status status-running is-planning');

    expect(executionStatusPillProps('waiting_on_dependencies')).toEqual({
      className: 'status status-waiting',
    });
    expect(executionStatusPillProps('awaiting_external')).toEqual({
      className: 'status status-awaiting_action',
    });
    expect(executionStatusPillProps('finalizing')).toEqual({
      className: 'status status-running',
    });
    expect(executionStatusPillProps('paused')).toEqual({ className: 'status status-neutral' });
    expect(executionStatusPillProps('canceled')).toEqual({ className: 'status status-cancelled' });
  });

  it('preserves MM-488 traceability for downstream verification', () => {
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.jiraIssue).toBe('MM-488');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.designRequirements).toEqual([
      'DESIGN-REQ-001',
      'DESIGN-REQ-002',
      'DESIGN-REQ-003',
      'DESIGN-REQ-004',
      'DESIGN-REQ-011',
      'DESIGN-REQ-013',
      'DESIGN-REQ-016',
    ]);
  });

  it('adds MM-489, MM-490, and MM-491 traceability for adjacent shimmer refinement stories', () => {
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-489');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-490');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-491');
  });
});
