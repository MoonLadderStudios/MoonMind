import { describe, expect, it } from 'vitest';

import {
  EXECUTING_STATUS_PILL_TRACEABILITY,
  executionStatusPillProps,
} from './executionStatusPillClasses';

describe('executionStatusPillProps', () => {
  it('adds shimmer selector metadata for active executing and transition status pills', () => {
    expect(executionStatusPillProps('executing')).toMatchObject({
      className: 'status status-running is-executing',
      'data-state': 'executing',
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': 'executing',
    });

    expect(executionStatusPillProps('running')).toMatchObject({
      className: 'status status-running is-running',
      'data-state': 'running',
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': 'running',
    });

    for (const key of ['initializing', 'planning', 'finalizing'] as const) {
      expect(executionStatusPillProps(key)).toMatchObject({
        className: `status status-${key} is-${key}`,
        'data-state': key,
        'data-effect': 'shimmer-sweep',
        'data-shimmer-label': key,
      });
    }

    expect(executionStatusPillProps('waiting')).toEqual({
      className: 'status status-waiting',
    });
  });

  it('can render active status colors without shimmer metadata for passive contexts', () => {
    expect(executionStatusPillProps('executing', { enableMotion: false })).toEqual({
      className: 'status status-running',
    });
    expect(executionStatusPillProps('running', { enableMotion: false })).toEqual({
      className: 'status status-running',
    });
    expect(executionStatusPillProps('initializing', { enableMotion: false })).toEqual({
      className: 'status status-initializing',
    });
    expect(executionStatusPillProps('planning', { enableMotion: false })).toEqual({
      className: 'status status-planning',
    });
    expect(executionStatusPillProps('finalizing', { enableMotion: false })).toEqual({
      className: 'status status-finalizing',
    });
  });

  it('keeps the existing class helper output for non-executing states across the MM-491 state matrix', () => {
    expect(executionStatusPillProps('completed')).toEqual({ className: 'status status-completed' });
    expect(executionStatusPillProps('failed')).toEqual({ className: 'status status-failed' });
    expect(executionStatusPillProps('executing').className).toBe('status status-running is-executing');
    expect(executionStatusPillProps('planning').className).toBe('status status-planning is-planning');
    expect(executionStatusPillProps('proposals')).toEqual({ className: 'status status-running' });

    expect(executionStatusPillProps('waiting_on_dependencies')).toEqual({
      className: 'status status-awaiting-dependencies',
    });
    expect(executionStatusPillProps('awaiting_external')).toEqual({
      className: 'status status-awaiting-external',
    });
    expect(executionStatusPillProps('paused')).toEqual({ className: 'status status-neutral' });
    expect(executionStatusPillProps('canceled')).toEqual({ className: 'status status-canceled' });
  });

  it('maps MM-1035 exact workflow states to their status color classes and MM-1036 shimmer metadata', () => {
    expect(executionStatusPillProps('scheduled')).toEqual({ className: 'status status-scheduled' });
    expect(executionStatusPillProps('awaiting_slot')).toEqual({ className: 'status status-awaiting-slot' });
    expect(executionStatusPillProps('waiting_on_dependencies')).toEqual({
      className: 'status status-awaiting-dependencies',
    });
    expect(executionStatusPillProps('awaiting_external')).toEqual({
      className: 'status status-awaiting-external',
    });
    expect(executionStatusPillProps('initializing')).toMatchObject({
      className: 'status status-initializing is-initializing',
      'data-effect': 'shimmer-sweep',
    });
    expect(executionStatusPillProps('planning')).toMatchObject({
      className: 'status status-planning is-planning',
      'data-effect': 'shimmer-sweep',
    });
    expect(executionStatusPillProps('finalizing')).toMatchObject({
      className: 'status status-finalizing is-finalizing',
      'data-effect': 'shimmer-sweep',
    });
    expect(executionStatusPillProps('canceled')).toEqual({ className: 'status status-canceled' });
  });

  it('uses the no-commit teal pill class for canonical and legacy no-commit statuses', () => {
    expect(executionStatusPillProps('no_commit')).toEqual({ className: 'status status-no-commit' });
    expect(executionStatusPillProps('no_changes')).toEqual({ className: 'status status-no-commit' });
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
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-704');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-1035');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toContain('MM-1036');
  });
});
