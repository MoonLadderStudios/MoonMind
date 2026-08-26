import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  WORKFLOW_STATUS_KEYS,
  WORKFLOW_STATUS_TRACEABILITY,
  formatWorkflowStatusLabel,
  isWorkflowLifecycleStatus,
  resolveWorkflowDisplayStatus,
  workflowStatusPillProps,
} from './workflowStatus';

describe('workflow status helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('covers every canonical workflow lifecycle status', () => {
    expect(WORKFLOW_STATUS_KEYS).toEqual([
      'scheduled',
      'initializing',
      'waiting_on_dependencies',
      'planning',
      'awaiting_slot',
      'executing',
      'awaiting_external',
      'proposals',
      'finalizing',
      'no_commit',
      'completed',
      'failed',
      'canceled',
    ]);

    for (const status of WORKFLOW_STATUS_KEYS) {
      expect(formatWorkflowStatusLabel(status, 'Unknown')).not.toBe('Unknown');
      expect(workflowStatusPillProps(status).className).not.toBe('status status-neutral');
    }
  });

  it('formats canonical workflow lifecycle states with readable labels and classes', () => {
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
    expect(workflowStatusPillProps('initializing')).toMatchObject({
      className: 'status status-initializing is-initializing',
    });
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
    expect(workflowStatusPillProps('finalizing')).toMatchObject({
      className: 'status status-finalizing is-finalizing',
    });
    expect(workflowStatusPillProps('no_commit')).toEqual({ className: 'status status-no-commit' });
    expect(workflowStatusPillProps('completed')).toEqual({ className: 'status status-completed' });
    expect(workflowStatusPillProps('failed')).toEqual({ className: 'status status-failed' });
    expect(workflowStatusPillProps('canceled')).toEqual({ className: 'status status-canceled' });
  });

  it('does not accept provider or step statuses as workflow states', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    for (const status of ['queued', 'succeeded', 'scheduling', 'awaiting_action', 'waiting']) {
      expect(workflowStatusPillProps(status)).toEqual({ className: 'status status-neutral' });
      expect(formatWorkflowStatusLabel(status, 'Unknown')).toBe('Unknown');
    }

    expect(warn).toHaveBeenCalledWith('Unknown workflow lifecycle status: queued');
    expect(warn).toHaveBeenCalledWith('Unknown workflow lifecycle status: waiting');
  });

  it('rejects compatibility aliases in canonical lifecycle helpers', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    for (const alias of ['no_changes', 'running']) {
      expect(isWorkflowLifecycleStatus(alias)).toBe(false);
      expect(workflowStatusPillProps(alias)).toEqual({ className: 'status status-neutral' });
      expect(formatWorkflowStatusLabel(alias, 'Unknown')).toBe('Unknown');
      expect(warn).toHaveBeenCalledWith(`Unknown workflow lifecycle status: ${alias}`);
    }
  });

  it('resolves the first recognized canonical display status from ordered candidates', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    // A raw provider value must not mask a canonical value later in the response.
    expect(resolveWorkflowDisplayStatus('unknown_raw_value', 'executing', 'running')).toBe('executing');
    // The display/API boundary repairs compatibility aliases before matching.
    expect(resolveWorkflowDisplayStatus('running', 'completed')).toBe('executing');
    expect(resolveWorkflowDisplayStatus('no_changes')).toBe('no_commit');
    expect(resolveWorkflowDisplayStatus(null, undefined, '', 'finalizing')).toBe('finalizing');
    expect(resolveWorkflowDisplayStatus('bogus', null)).toBeNull();
    expect(resolveWorkflowDisplayStatus()).toBeNull();

    expect(warn).not.toHaveBeenCalled();
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
