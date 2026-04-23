import { describe, expect, it } from 'vitest';

import {
  EXECUTING_STATUS_PILL_TRACEABILITY,
  executionStatusPillProps,
} from './executionStatusPillClasses';

describe('executionStatusPillProps', () => {
  it('adds executing shimmer selector metadata only for executing status pills', () => {
    expect(executionStatusPillProps('executing')).toMatchObject({
      className: 'status status-running is-executing',
      'data-state': 'executing',
      'data-effect': 'shimmer-sweep',
    });

    expect(executionStatusPillProps('running')).toEqual({
      className: 'status status-running',
    });
    expect(executionStatusPillProps('waiting')).toEqual({
      className: 'status status-waiting',
    });
  });

  it('keeps the existing class helper output for non-executing states', () => {
    expect(executionStatusPillProps('completed').className).toBe('status status-completed');
    expect(executionStatusPillProps('failed').className).toBe('status status-failed');
    expect(executionStatusPillProps('executing').className).toBe('status status-running is-executing');
  });

  it('preserves MM-488 traceability for downstream verification', () => {
    expect(EXECUTING_STATUS_PILL_TRACEABILITY).toEqual({
      jiraIssue: 'MM-488',
      designRequirements: [
        'DESIGN-REQ-001',
        'DESIGN-REQ-002',
        'DESIGN-REQ-003',
        'DESIGN-REQ-004',
        'DESIGN-REQ-011',
        'DESIGN-REQ-013',
        'DESIGN-REQ-016',
      ],
    });
  });
});
