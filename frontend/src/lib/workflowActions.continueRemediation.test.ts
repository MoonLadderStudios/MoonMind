import { describe, expect, it } from 'vitest';
import {
  buildContinueRemediationRequest,
  continuedWorkflowHref,
} from './workflowActions';

describe('control-stop remediation workflow actions', () => {
  it('builds a bounded retry-stable continuation grant', () => {
    expect(buildContinueRemediationRequest('stop:verify:6', {
      maxAttempts: 4,
      maxConsecutiveNoProgressAttempts: 2,
    })).toEqual({
      controlStopId: 'stop:verify:6',
      continuationBudget: {
        grantId: 'workflow-detail:stop:verify:6:4:2',
        maxAttempts: 4,
        maxConsecutiveNoProgressAttempts: 2,
        consumedAttempts: 0,
        consecutiveNoProgressAttempts: 0,
      },
    });
  });

  it('rejects missing identity and unbounded or inconsistent budgets', () => {
    expect(() => buildContinueRemediationRequest(' ', {
      maxAttempts: 3,
      maxConsecutiveNoProgressAttempts: 2,
    })).toThrow('control-stop identity');
    expect(() => buildContinueRemediationRequest('stop-1', {
      maxAttempts: 101,
      maxConsecutiveNoProgressAttempts: 2,
    })).toThrow('between 1 and 100');
    expect(() => buildContinueRemediationRequest('stop-1', {
      maxAttempts: 2,
      maxConsecutiveNoProgressAttempts: 3,
    })).toThrow('between 1 and the remediation attempt limit');
  });

  it('links to the deterministic destination workflow detail', () => {
    expect(continuedWorkflowHref('continuation/source:1')).toBe(
      '/workflows/continuation%2Fsource%3A1',
    );
  });
});
