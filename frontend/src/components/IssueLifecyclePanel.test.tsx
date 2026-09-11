import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, waitFor, cleanup } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import { IssueLifecyclePanel, issueLifecycleEvidenceFromExecution } from './IssueLifecyclePanel';

// MoonLadderStudios/MoonMind#4183: recovery-status projection in Workflow
// Detail. The panel owns no lifecycle state engine: it renders the
// server-derived context and offers the supported operator actions with
// progressive disclosure for advanced evidence.

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubLifecycleResponse(payload: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => payload })) as unknown as typeof fetch,
  );
}

const PROJECTION = {
  context: {
    issue_ref: 'o/r#4183',
    issue_url: 'https://github.com/o/r/issues/4183',
    issue_state: 'open',
    settled_lifecycle_state: 'recovery_needed',
    current_attempt: { attempt_id: 'att_aaa' },
    predecessor_attempts: [{ attempt_id: 'att_000', result: 'failed' }],
    originating_deployment_id: 'dep-origin',
    preserved_pr: { pr_url: 'https://github.com/o/r/pull/7' },
    preserved_revision: 'b'.repeat(40),
    remaining_requirements: ['finish repair'],
    recovery_phase: 'continuation_ready',
    operator_hold: { active: false },
    evidence_freshness: 'fresh',
    evidence_completeness: 'complete',
    sync_status: 'fresh',
    pending_sync_errors: [],
    failure_history: [{ attempt_id: 'att_000', result: 'failed' }],
    prior_failure_count: 1,
    competing_refs_preserved: [],
  },
  attention_category: null,
  recovery_availability: { available: true, reason: 'continuation_ready', detail: 'A safe continuation handoff is available.' },
  actions: [
    { action: 'continue_work', enabled: true, continue_variant: 'code_seeded_continuation' },
    { action: 'hold_processing', enabled: true },
    { action: 'acknowledge_incident', enabled: true },
    { action: 'resolve_conflict', enabled: false, disabled_reason: 'no_competing_refs' },
    { action: 'authorize_retry', enabled: false, disabled_reason: 'retry_not_blocked' },
    { action: 'abandon_work', enabled: true },
  ],
  continue_variants: ['exact_checkpoint_resume', 'code_seeded_continuation', 'verification_only', 'publication_status_finalization'],
};

describe('issueLifecycleEvidenceFromExecution', () => {
  it('returns empty evidence when no issue is linked instead of fabricating ownership', () => {
    expect(issueLifecycleEvidenceFromExecution({ workflowId: 'w-1', inputParameters: {} })).toEqual({});
    expect(issueLifecycleEvidenceFromExecution(null)).toEqual({});
  });

  it('reads the portable github_issue shapes from input parameters', () => {
    expect(
      issueLifecycleEvidenceFromExecution({
        inputParameters: { github_issue: { repository: 'o/r', issue_number: 4183 } },
      }),
    ).toMatchObject({ repository: 'o/r', issueNumber: 4183 });
  });
});

describe('IssueLifecyclePanel', () => {
  it('renders an honest empty state when no issue is linked', () => {
    renderWithClient(<IssueLifecyclePanel apiBase="/api" evidence={{}} />);
    expect(screen.getByTestId('issue-lifecycle-empty')).not.toBeNull();
  });

  it('renders lineage, recovery availability, and actions from the server projection', async () => {
    stubLifecycleResponse(PROJECTION);
    renderWithClient(
      <IssueLifecyclePanel apiBase="/api" evidence={{ repository: 'o/r', issueNumber: 4183 }} />,
    );
    await waitFor(() => expect(screen.getByTestId('issue-lifecycle-panel')).not.toBeNull());
    expect(screen.getByText(/A safe continuation handoff is available/)).not.toBeNull();
    expect((screen.getByRole('button', { name: 'Continue existing work' }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: 'Resolve competing-attempt conflict' }) as HTMLButtonElement).disabled).toBe(true);
    // Prior failure history is preserved and visible.
    expect(screen.getByText(/Prior failed attempts preserved: 1/)).not.toBeNull();
    // Advanced evidence is progressively disclosed behind a closed
    // <details> element, not shown by default.
    const details = document.querySelector('[data-testid="issue-lifecycle-panel"] details');
    expect(details).not.toBeNull();
    expect((details as HTMLDetailsElement).open).toBe(false);
  });

  it('reports pending/unknown rather than a false remote state on fetch failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down');
      }) as unknown as typeof fetch,
    );
    renderWithClient(
      <IssueLifecyclePanel apiBase="/api" evidence={{ repository: 'o/r', issueNumber: 4183 }} />,
    );
    await waitFor(() =>
      expect(screen.getByText(/Recovery status is pending\/unknown/)).not.toBeNull(),
    );
  });
});
