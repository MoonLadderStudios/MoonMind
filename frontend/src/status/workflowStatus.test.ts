import { describe, expect, it } from 'vitest';

import {
  WORKFLOW_STATUS_TRACEABILITY,
  formatWorkflowStatusLabel,
  workflowStatusPillProps,
} from './workflowStatus';

describe('workflow status helpers', () => {
  it('formats canonical workflow lifecycle states with readable labels', () => {
    expect(formatWorkflowStatusLabel('awaiting_slot')).toBe('Awaiting slot');
    expect(formatWorkflowStatusLabel('waiting_on_dependencies')).toBe('Awaiting dependencies');
    expect(formatWorkflowStatusLabel('awaiting_external')).toBe('Awaiting external');
    expect(formatWorkflowStatusLabel('no_commit')).toBe('No commit');
  });

  it('does not accept provider, step, or legacy dashboard aliases as workflow states', () => {
    for (const status of ['queued', 'succeeded', 'running', 'scheduling', 'awaiting_action', 'waiting', 'no_changes']) {
      expect(workflowStatusPillProps(status)).toEqual({ className: 'status status-neutral' });
      expect(formatWorkflowStatusLabel(status, 'Unknown')).toBe('Unknown');
    }
  });

  it('uses kebab-case classes for canonical workflow lifecycle states', () => {
    expect(workflowStatusPillProps('scheduled')).toEqual({ className: 'status status-scheduled' });
    expect(workflowStatusPillProps('awaiting_slot')).toEqual({ className: 'status status-awaiting-slot' });
    expect(workflowStatusPillProps('waiting_on_dependencies')).toEqual({
      className: 'status status-awaiting-dependencies',
    });
    expect(workflowStatusPillProps('awaiting_external')).toEqual({
      className: 'status status-awaiting-external',
    });
    expect(workflowStatusPillProps('no_commit')).toEqual({ className: 'status status-no-commit' });
  });

  it('adds shimmer metadata only for active workflow states that own the effect', () => {
    expect(workflowStatusPillProps('executing')).toMatchObject({
      className: 'status status-running is-executing',
      'data-state': 'executing',
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': 'Executing',
    });
    expect(workflowStatusPillProps('planning')).toMatchObject({
      className: 'status status-planning is-planning',
      'data-state': 'planning',
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': 'Planning',
    });
    expect(workflowStatusPillProps('executing', { enableMotion: false })).toEqual({
      className: 'status status-running',
    });
  });

  it('preserves adjacent execution-pill traceability', () => {
    expect(WORKFLOW_STATUS_TRACEABILITY.jiraIssue).toBe('MM-488');
    expect(WORKFLOW_STATUS_TRACEABILITY.relatedJiraIssues).toContain('MM-1073');
  });
});

