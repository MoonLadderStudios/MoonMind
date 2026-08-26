import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  FollowUpRetrievalDiagnosticsSection,
  type FollowUpRetrievalDiagnostics,
} from './workflow-detail';

// AC10 (MoonLadderStudios/MoonMind#3514): Workflow Detail exposes follow-up
// retrieval request count, budget usage, per-request diagnostics, capability
// expiry/revocation, and aggregate telemetry.

describe('FollowUpRetrievalDiagnosticsSection', () => {
  it('reports when no follow-up retrieval capability was issued', () => {
    render(
      <FollowUpRetrievalDiagnosticsSection
        diagnostics={{
          bridgeSessionId: 'bridge-1',
          capabilityCount: 0,
          capabilities: [],
          aggregate: {},
        }}
        isLoading={false}
        error={null}
      />,
    );
    expect(
      screen.getByText(/no in-session retrieval capability issued/i),
    ).toBeTruthy();
  });

  it('renders aggregate telemetry and per-capability diagnostics', () => {
    const diagnostics: FollowUpRetrievalDiagnostics = {
      bridgeSessionId: 'bridge-1',
      capabilityCount: 1,
      aggregate: {
        requestCount: 3,
        succeeded: 2,
        empty: 1,
        denied: 0,
        failed: 1,
        fallback: 1,
        truncated: 1,
        budgetExhausted: 1,
        timedOut: 0,
        delivered: 1,
        notDelivered: 1,
        deliveryUnknown: 1,
        cancelled: 0,
        maxLatencyMs: 1200,
        totalContextBytes: 4096,
        activeCapabilities: 1,
        expiredCapabilities: 0,
        revokedCapabilities: 0,
      },
      capabilities: [
        {
          capabilityId: 'rcap_abc',
          state: 'active',
          expiresAt: 1_000_000,
          queryCount: 3,
          maxQueries: 12,
          collections: ['repo', 'docs'],
          policyVersion: 'policy-7',
          overlayPolicy: 'include',
          fallbackAllowed: true,
          scope: { repository: 'MoonMind', run: 'run-1' },
          requests: [
            {
              evidenceRef: 'artifact://retrieval-follow-up/run-1/e1',
              state: 'succeeded',
              resultCount: 2,
              latencyMs: 900,
              truncated: false,
              delivery: { state: 'delivered' },
              contextPackRef: '/retrieval/capabilities/rcap_abc/results/tool-1',
            },
            {
              state: 'failed',
              classification: 'token_budget_exhausted',
              resultCount: 0,
              delivery: { state: 'not_delivered' },
            },
          ],
        },
      ],
    };
    render(
      <FollowUpRetrievalDiagnosticsSection
        diagnostics={diagnostics}
        isLoading={false}
        error={null}
      />,
    );

    expect(screen.getByText(/3 requests across 1 capability/i)).toBeTruthy();
    // Per-request state and classification are shown.
    expect(screen.getByText(/token_budget_exhausted/)).toBeTruthy();
    // Capability scope + collections + expiry.
    expect(screen.getByText(/repository=MoonMind/)).toBeTruthy();
    expect(screen.getByText(/repo, docs/)).toBeTruthy();
    // Aggregate telemetry lines.
    expect(screen.getByText(/max latency 1200ms/i)).toBeTruthy();
  });

  it('shows an unavailable message on error', () => {
    render(
      <FollowUpRetrievalDiagnosticsSection
        diagnostics={null}
        isLoading={false}
        error={new Error('boom')}
      />,
    );
    expect(
      screen.getByText(/diagnostics are unavailable/i),
    ).toBeTruthy();
  });
});
