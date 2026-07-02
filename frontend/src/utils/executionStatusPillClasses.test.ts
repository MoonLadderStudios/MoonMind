import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  EXECUTING_STATUS_PILL_TRACEABILITY,
  STEP_LEDGER_STATUSES,
  WORKFLOW_LIFECYCLE_STATUSES,
  integrationProviderStatusPillView,
  isStepLedgerStatus,
  isWorkflowLifecycleStatus,
  stepLedgerStatusPillView,
  workflowLifecycleStatusPillView,
} from './executionStatusPillClasses';

describe('domain status pill helpers', () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    warnSpy.mockRestore();
  });

  it('generates labels and kebab-case classes for every canonical workflow lifecycle status', () => {
    for (const status of WORKFLOW_LIFECYCLE_STATUSES) {
      const view = workflowLifecycleStatusPillView(status, { enableMotion: false });
      expect(view.label).toMatch(/^[A-Z]/);
      expect(view.label).not.toMatch(/^[A-Z_ ]{3,}$/);
      expect(view.pillProps.className).toContain('status ');
      expect(view.pillProps.className).not.toContain('_');
    }

    expect(workflowLifecycleStatusPillView('completed').pillProps).toEqual({
      className: 'status status-completed',
    });
    expect(workflowLifecycleStatusPillView('failed').pillProps).toEqual({
      className: 'status status-failed',
    });
    expect(workflowLifecycleStatusPillView('executing').pillProps).toMatchObject({
      className: 'status status-running is-executing',
      'data-state': 'executing',
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': 'Executing',
    });
    expect(workflowLifecycleStatusPillView('scheduled').pillProps).toEqual({
      className: 'status status-scheduled',
    });
    expect(workflowLifecycleStatusPillView('awaiting_slot').pillProps).toEqual({
      className: 'status status-awaiting-slot',
    });
    expect(workflowLifecycleStatusPillView('waiting_on_dependencies').pillProps).toEqual({
      className: 'status status-waiting-on-dependencies',
    });
    expect(workflowLifecycleStatusPillView('awaiting_external').pillProps).toEqual({
      className: 'status status-awaiting-external',
    });
    expect(workflowLifecycleStatusPillView('initializing').pillProps.className).toBe(
      'status status-initializing is-initializing',
    );
    expect(workflowLifecycleStatusPillView('planning').pillProps.className).toBe(
      'status status-planning is-planning',
    );
    expect(workflowLifecycleStatusPillView('finalizing').pillProps.className).toBe(
      'status status-finalizing is-finalizing',
    );
    expect(workflowLifecycleStatusPillView('canceled').pillProps).toEqual({
      className: 'status status-canceled',
    });
    expect(workflowLifecycleStatusPillView('no_commit').label).toBe('No commit');
    expect(workflowLifecycleStatusPillView('no_commit').pillProps).toEqual({
      className: 'status status-no-commit',
    });
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it('generates labels and kebab-case classes for every canonical step ledger status', () => {
    for (const status of STEP_LEDGER_STATUSES) {
      const view = stepLedgerStatusPillView(status, { enableMotion: false });
      expect(view.label).toMatch(/^[A-Z]/);
      expect(view.label).not.toMatch(/^[A-Z_ ]{3,}$/);
      expect(view.pillProps.className).toContain('status ');
      expect(view.pillProps.className).not.toContain('_');
    }

    expect(stepLedgerStatusPillView('pending').pillProps).toEqual({
      className: 'status status-pending',
    });
    expect(stepLedgerStatusPillView('ready').pillProps).toEqual({
      className: 'status status-ready',
    });
    expect(stepLedgerStatusPillView('running').pillProps).toMatchObject({
      className: 'status status-running is-running',
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': 'Running',
    });
    expect(stepLedgerStatusPillView('awaiting_external').pillProps).toEqual({
      className: 'status status-awaiting-external',
    });
    expect(stepLedgerStatusPillView('reviewing').pillProps).toEqual({
      className: 'status status-reviewing',
    });
    expect(stepLedgerStatusPillView('succeeded').pillProps).toEqual({
      className: 'status status-succeeded',
    });
    expect(stepLedgerStatusPillView('failed').pillProps).toEqual({
      className: 'status status-failed',
    });
    expect(stepLedgerStatusPillView('skipped').pillProps).toEqual({
      className: 'status status-skipped',
    });
    expect(stepLedgerStatusPillView('canceled').pillProps).toEqual({
      className: 'status status-canceled',
    });
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it('rejects workflow-only statuses in the step ledger helper with a neutral diagnostic fallback', () => {
    for (const workflowOnlyStatus of ['scheduled', 'initializing', 'planning', 'finalizing', 'no_commit']) {
      expect(isStepLedgerStatus(workflowOnlyStatus)).toBe(false);
      expect(stepLedgerStatusPillView(workflowOnlyStatus)).toEqual({
        label: expect.any(String),
        pillProps: { className: 'status status-neutral' },
      });
    }

    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Unknown step ledger status "scheduled"'),
    );
  });

  it('keeps integration/provider statuses out of the workflow lifecycle domain', () => {
    for (const integrationStatus of ['running', 'succeeded', 'no_changes', 'awaiting_action', 'waiting']) {
      expect(isWorkflowLifecycleStatus(integrationStatus)).toBe(false);
      expect(workflowLifecycleStatusPillView(integrationStatus, { enableMotion: false }).pillProps).toEqual({
        className: 'status status-neutral',
      });
    }

    expect(integrationProviderStatusPillView('running', { enableMotion: false })).toEqual({
      label: 'Running',
      pillProps: { className: 'status status-running' },
    });
    expect(integrationProviderStatusPillView('succeeded')).toEqual({
      label: 'Succeeded',
      pillProps: { className: 'status status-completed' },
    });
    expect(integrationProviderStatusPillView('awaiting_action')).toEqual({
      label: 'Awaiting action',
      pillProps: { className: 'status status-awaiting-action' },
    });
  });

  it('maps legacy no_changes to No commit only at the integration/provider compatibility boundary', () => {
    expect(workflowLifecycleStatusPillView('no_changes')).toEqual({
      label: 'No changes',
      pillProps: { className: 'status status-neutral' },
    });
    expect(stepLedgerStatusPillView('no_changes')).toEqual({
      label: 'No changes',
      pillProps: { className: 'status status-neutral' },
    });
    expect(integrationProviderStatusPillView('no_changes')).toEqual({
      label: 'No commit',
      pillProps: { className: 'status status-no-commit' },
    });
  });

  it('renders unknown statuses neutrally and emits developer diagnostics without crashing', () => {
    expect(workflowLifecycleStatusPillView('prototype_state')).toEqual({
      label: 'Prototype state',
      pillProps: { className: 'status status-neutral' },
    });
    expect(stepLedgerStatusPillView(null)).toEqual({
      label: 'Unknown',
      pillProps: { className: 'status status-neutral' },
    });
    expect(integrationProviderStatusPillView('provider_wait')).toEqual({
      label: 'Provider wait',
      pillProps: { className: 'status status-neutral' },
    });

    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Unknown workflow lifecycle status "prototype_state"'),
    );
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Unknown step ledger status "(empty)"'),
    );
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Unknown integration/provider status "provider_wait"'),
    );
  });

  it('preserves status pill traceability for downstream verification including MM-1083', () => {
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.jiraIssue).toBe('MM-488');
    expect(EXECUTING_STATUS_PILL_TRACEABILITY.relatedJiraIssues).toEqual(
      expect.arrayContaining(['MM-1073', 'MM-1083']),
    );
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
});
