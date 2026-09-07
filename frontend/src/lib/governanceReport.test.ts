import { describe, expect, it } from 'vitest';

import {
  governanceReportExplanation,
  governanceReportHref,
  isGovernanceReportStatus,
  normalizeGovernanceReportStatus,
} from './governanceReport';

describe('governance report link helpers', () => {
  it('builds evidence hrefs with and without a report id', () => {
    expect(governanceReportHref('mm:123', 'govrep_abc')).toBe('/workflows/mm%3A123/evidence?report=govrep_abc');
    expect(governanceReportHref('mm:123')).toBe('/workflows/mm%3A123/evidence');
    expect(governanceReportHref('mm:123', '  ')).toBe('/workflows/mm%3A123/evidence');
  });

  it('accepts only the four server-owned statuses', () => {
    expect(isGovernanceReportStatus('ready')).toBe(true);
    expect(isGovernanceReportStatus('partial')).toBe(true);
    expect(isGovernanceReportStatus('pending')).toBe(true);
    expect(isGovernanceReportStatus('failed')).toBe(true);
    expect(isGovernanceReportStatus('pass')).toBe(false);
    expect(isGovernanceReportStatus('approved')).toBe(false);
    expect(isGovernanceReportStatus(undefined)).toBe(false);
  });

  it('never upgrades unknown statuses and prefers server copy', () => {
    expect(normalizeGovernanceReportStatus('bogus')).toBe('pending');
    expect(governanceReportExplanation('bogus')).toContain('pending');
    expect(governanceReportExplanation('ready', 'server copy')).toBe('server copy');
    expect(governanceReportExplanation('failed')).toContain('auxiliary');
  });
});
