import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { renderWithClient } from '../../utils/test-utils';
import { SecurityFindingsPanel } from './SecurityFindingsPanel';
afterEach(() => vi.restoreAllMocks());
it('shows evidence-backed disposition and opens only the selected finding', async () => {
  const onSelect = vi.fn();
  vi.spyOn(window, 'fetch').mockResolvedValue({ ok: true, json: async () => ({ comparisonArtifactRef: 'art_cmp', findings: [{
    comparisonArtifactRef: 'art_cmp', findingId: 'ownership', comparisonDigest: 'digest', coverageStatus: 'stale_or_partial', intelligenceChanges: ['rules changed'],
    finding: { findingId: 'ownership', state: 'not_reassessed', evidenceExpiresAt: '2026-10-01T00:00:00Z', changedIntelligence: [], finding: { nativeId: 'ownership', asset: 'app', ruleId: 'access', evidenceRefs: ['art_native'] } },
    decisions: [{ status: 'review_required', reviewReasons: ['retest_contradicted'], decision: { disposition: 'fix_retest', reason: 'Counterexample still fails', retestOutcome: 'inconclusive', reviewAt: '2026-10-01T00:00:00Z', reviewConditions: ['repeat original test'], evidenceRefs: ['art_retest'] } }],
  }] }) } as Response);
  renderWithClient(<SecurityFindingsPanel workflowId="source" runId="run" apiBase="/api" artifacts={[{ artifactId: 'art_cmp', links: [{ linkType: 'security.comparison' }] }]} onSelect={onSelect} />);
  fireEvent.change(screen.getByLabelText('Security comparison'), { target: { value: 'art_cmp' } });
  expect(await screen.findByText('Counterexample still fails')).toBeTruthy();
  expect(screen.getByText('not_reassessed')).toBeTruthy();
  expect(screen.getByText(/retest_contradicted/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Investigate ownership' }));
  expect(onSelect).toHaveBeenCalledWith({ comparisonArtifactRef: 'art_cmp', findingId: 'ownership' });
  expect(window.fetch).toHaveBeenCalledWith('/api/executions/source/remediation-security-findings?comparisonArtifactRef=art_cmp&runId=run', expect.objectContaining({ credentials: 'same-origin' }));
});
it('does not expose a repair action when evidence verification fails', async () => {
  vi.spyOn(window, 'fetch').mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: { message: 'Comparison dependencies changed' } }) } as Response);
  renderWithClient(<SecurityFindingsPanel workflowId="source" runId="run" apiBase="/api" artifacts={[{ artifactId: 'art_cmp', links: [{ linkType: 'security.comparison' }] }]} onSelect={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Security comparison'), { target: { value: 'art_cmp' } });
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.queryByRole('button', { name: /Investigate/ })).toBeNull();
});
it('rejects a response for another comparison instead of starting from changed evidence', async () => {
  vi.spyOn(window, 'fetch').mockResolvedValue({ ok: true, json: async () => ({ comparisonArtifactRef: 'art_other', findings: [] }) } as Response);
  renderWithClient(<SecurityFindingsPanel workflowId="source" runId="run" apiBase="/api" artifacts={[{ artifactId: 'art_cmp', links: [{ linkType: 'security.comparison' }] }]} onSelect={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Security comparison'), { target: { value: 'art_cmp' } });
  expect((await screen.findByRole('alert')).textContent).toContain('selection changed');
  expect(screen.queryByRole('button', { name: /Investigate/ })).toBeNull();
});
