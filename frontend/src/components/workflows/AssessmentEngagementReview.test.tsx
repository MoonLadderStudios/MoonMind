import { describe, expect, it } from 'vitest';
import { renderWithClient, screen } from '../../utils/test-utils';
import { AssessmentEngagementReview } from './AssessmentEngagementReview';

describe('rules of engagement display', () => {
  it('renders all authoritative sections without turning context or a checkbox into authority', () => {
    const sections = [
      { label: 'Targets and exclusions', items: ['payments.example.test:443; exclude /admin'] },
      { label: 'Allowed actions', items: ['Read-only TLS inspection'] },
      { label: 'Identity and network references', items: ['db://cloud-reader at revision 2; isolated-egress'] },
      { label: 'Rate and resource limits', items: ['10 requests/second; 1000 millicores; 1024 MiB; 120 cumulative seconds'] },
      { label: 'Validity', items: ['2026-01-01T00:00:00Z until 2026-01-02T00:00:00Z'] },
      { label: 'Evidence intent', items: ['Retain the certificate report'] },
      { label: 'Stop conditions', items: ['Stop on expiry, cancellation, revocation, or exhausted budget'] },
    ];
    renderWithClient(<AssessmentEngagementReview historical review={{ schemaVersion: 'assessment-engagement/v1', authorizationRef: 'payments', authorizationGeneration: 'generation-3', proposalDigest: 'proposal', scopeDigest: 'scope', scope: {}, sections, limitations: ['No exploitation'] }} />);
    for (const section of sections) expect(screen.getByText(section.items[0]!)).toBeTruthy();
    expect(screen.getByText(/not a renewed authorization/)).toBeTruthy();
    expect(screen.getByText(/Retained evidence remains available/)).toBeTruthy();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });
});
