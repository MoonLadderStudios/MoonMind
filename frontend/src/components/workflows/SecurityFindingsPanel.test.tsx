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
  fireEvent.change(screen.getByLabelText('Security evidence'), { target: { value: 'art_cmp' } });
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
  fireEvent.change(screen.getByLabelText('Security evidence'), { target: { value: 'art_cmp' } });
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.queryByRole('button', { name: /Investigate/ })).toBeNull();
});
it('rejects a response for another comparison instead of starting from changed evidence', async () => {
  vi.spyOn(window, 'fetch').mockResolvedValue({ ok: true, json: async () => ({ comparisonArtifactRef: 'art_other', findings: [] }) } as Response);
  renderWithClient(<SecurityFindingsPanel workflowId="source" runId="run" apiBase="/api" artifacts={[{ artifactId: 'art_cmp', links: [{ linkType: 'security.comparison' }] }]} onSelect={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Security evidence'), { target: { value: 'art_cmp' } });
  expect((await screen.findByRole('alert')).textContent).toContain('selection changed');
  expect(screen.queryByRole('button', { name: /Investigate/ })).toBeNull();
});
it('shows agent hypotheses and candidate without converting coverage into verification', async () => {
  const onSelect = vi.fn();
  vi.spyOn(window, 'fetch').mockResolvedValue({ ok: true, json: async () => ({ reviewArtifactRef: 'art_review', findings: [{
    sourceKind: 'agent_review', reviewArtifactRef: 'art_review', reviewDigest: 'digest', interpretationId: 'ownership', candidateRef: 'sha256:original',
    interpretation: { id: 'ownership', hypothesis: 'Caller can read another owner invoice', disposition: 'confirmed', evidenceStatus: 'supported', evidenceRefs: ['art_probe'], limitations: ['Controlled fixture'], subject: { asset: 'app', locator: 'get_invoice:4', identity: 'alice', resource: 'invoice-bob' } },
    scope: { assets: ['app'], exclusions: [], repositoryExceptions: [] }, threatModel: { entryPoints: ['get_invoice'], trustBoundaries: ['owner'], invariants: ['owner-only'] }, limitations: ['No live deployment'],
    retests: [{ hypothesisId: 'ownership', originalEvidenceRefs: ['art_probe'], currentEvidenceRefs: ['art_after'], changedAssets: ['app'], coveredAssets: ['app'], coverage: 'complete', limitations: ['Recorded job evidence owns outcome'] }],
  }] }) } as Response);
  renderWithClient(<SecurityFindingsPanel workflowId="source" runId="run" apiBase="/api" artifacts={[{ artifactId: 'art_review', links: [{ linkType: 'security.review' }] }]} onSelect={onSelect} />);
  fireEvent.change(screen.getByLabelText('Security evidence'), { target: { value: 'art_review' } });
  expect(await screen.findByText('Caller can read another owner invoice')).toBeTruthy();
  expect(screen.getByText(/sha256:original/)).toBeTruthy();
  expect(screen.queryByText('remediated_verified')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Investigate ownership' }));
  expect(onSelect).toHaveBeenCalledWith({ reviewArtifactRef: 'art_review', interpretationId: 'ownership' });
});
