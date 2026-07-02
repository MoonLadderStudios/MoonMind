import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  WORKFLOW_STATUS_TRACEABILITY,
  formatWorkflowCompatibilityStatusLabel,
  formatWorkflowStatusLabel,
  workflowCompatibilityStatusPillProps,
  workflowStatusPillProps,
} from './workflowStatus';

describe('workflow status helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('formats every canonical workflow lifecycle state with readable labels and classes', () => {
    expect(formatWorkflowStatusLabel('scheduled')).toBe('Scheduled');
    expect(formatWorkflowStatusLabel('initializing')).toBe('Initializing');
    expect(formatWorkflowStatusLabel('waiting_on_dependencies')).toBe('Awaiting dependencies');
    expect(formatWorkflowStatusLabel('planning')).toBe('Planning');
    expect(formatWorkflowStatusLabel('awaiting_slot')).toBe('Awaiting slot');
    expect(formatWorkflowStatusLabel('executing')).toBe('Executing');
    expect(formatWorkflowStatusLabel('awaiting_external')).toBe('Awaiting external');
    expect(formatWorkflowStatusLabel('proposals')).toBe('Proposals');
    expect(formatWorkflowStatusLabel('finalizing')).toBe('Finalizing');
    expect(formatWorkflowStatusLabel('no_commit')).toBe('No commit');
    expect(formatWorkflowStatusLabel('completed')).toBe('Completed');
    expect(formatWorkflowStatusLabel('failed')).toBe('Failed');
    expect(formatWorkflowStatusLabel('canceled')).toBe('Canceled');

    expect(workflowStatusPillProps('scheduled')).toEqual({ className: 'status status-scheduled' });
    expect(workflowStatusPillProps('initializing')).toMatchObject({ className: 'status status-initializing is-initializing' });
    expect(workflowStatusPillProps('waiting_on_dependencies')).toEqual({
      className: 'status status-awaiting-dependencies',
    });
    expect(workflowStatusPillProps('planning')).toMatchObject({ className: 'status status-planning is-planning' });
    expect(workflowStatusPillProps('awaiting_slot')).toEqual({ className: 'status status-awaiting-slot' });
    expect(workflowStatusPillProps('executing')).toMatchObject({ className: 'status status-running is-executing' });
    expect(workflowStatusPillProps('awaiting_external')).toEqual({
      className: 'status status-awaiting-external',
    });
    expect(workflowStatusPillProps('proposals')).toEqual({ className: 'status status-running' });
    expect(workflowStatusPillProps('finalizing')).toMatchObject({ className: 'status status-finalizing is-finalizing' });
    expect(workflowStatusPillProps('no_commit')).toEqual({ className: 'status status-no-commit' });
    expect(workflowStatusPillProps('completed')).toEqual({ className: 'status status-completed' });
    expect(workflowStatusPillProps('failed')).toEqual({ className: 'status status-failed' });
    expect(workflowStatusPillProps('canceled')).toEqual({ className: 'status status-canceled' });
  });

  it('does not accept provider, step, or legacy dashboard aliases as workflow states', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    for (const status of ['queued', 'succeeded', 'running', 'scheduling', 'awaiting_action', 'waiting', 'no_changes']) {
      expect(workflowStatusPillProps(status)).toEqual({ className: 'status status-neutral' });
      expect(formatWorkflowStatusLabel(status, 'Unknown')).toBe('Unknown');
    }

    expect(warn).toHaveBeenCalledWith('Unknown workflow lifecycle status: queued');
    expect(warn).toHaveBeenCalledWith('Unknown workflow lifecycle status: no_changes');
  });

  it('maps legacy no_changes only at the explicit workflow compatibility boundary', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});

    expect(workflowStatusPillProps('no_changes')).toEqual({ className: 'status status-neutral' });
    expect(formatWorkflowStatusLabel('no_changes', 'Unknown')).toBe('Unknown');

    expect(workflowCompatibilityStatusPillProps('no_changes')).toEqual({
      className: 'status status-no-commit',
    });
    expect(formatWorkflowCompatibilityStatusLabel('no_changes')).toBe('No commit');
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
    expect(WORKFLOW_STATUS_TRACEABILITY.relatedJiraIssues).toContain('MM-1083');
  });
});
