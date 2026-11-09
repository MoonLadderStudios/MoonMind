import { describe, expect, it } from 'vitest';
import { renderWithClient, screen } from '../../utils/test-utils';
import { AssessmentOccurrenceEvidence } from './AssessmentOccurrenceEvidence';

describe('recurring assessment occurrence evidence', () => {
  it('distinguishes run authority from observed coverage and reports gaps', () => {
    renderWithClient(<AssessmentOccurrenceEvidence value={{ authorizationRef: 'payments', authorizationGeneration: 'generation-2', ordinal: 3, admittedAt: '2026-01-01T00:00:00Z', expiresAt: '2026-01-01T00:10:00Z', coverage: 'unverified', comparisonDisposition: 'unverified', scopeDigest: 'scope-digest', evidenceRefs: [], limitations: ['Current pack evidence unavailable'] }} />);
    expect(screen.getByText('Current pack evidence unavailable')).toBeTruthy();
    expect(screen.getByText(/Admission is not an observation/)).toBeTruthy();
    expect(screen.getByText(/No observation evidence recorded/)).toBeTruthy();
    expect(screen.getByText(/generation-2/)).toBeTruthy();
  });
  it('does not invent evidence when no occurrence was recorded', () => {
    renderWithClient(<AssessmentOccurrenceEvidence value={undefined} />);
    expect(screen.queryByRole('region')).toBeNull();
  });
});
