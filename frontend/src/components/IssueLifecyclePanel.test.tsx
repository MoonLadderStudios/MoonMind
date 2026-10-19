import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
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
  it('renders nothing and makes no request when no issue is linked', () => {
    const fetchSpy = vi.spyOn(window, 'fetch');
    const { container } = renderWithClient(<IssueLifecyclePanel apiBase="/api" evidence={{}} />);
    expect(container.textContent).toBe('');
    expect(screen.queryByRole('heading', { name: 'Issue lifecycle' })).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('renders lineage, recovery availability, and actions from the server projection', async () => {
    stubLifecycleResponse(PROJECTION);
    renderWithClient(
      <IssueLifecyclePanel apiBase="/api" evidence={{ repository: 'o/r', issueNumber: 4183 }} />,
    );
    await waitFor(() => expect(screen.getByTestId('issue-lifecycle-panel')).not.toBeNull());
    expect(screen.getByText(/A safe continuation handoff is available/)).not.toBeNull();
    // Continuation requires explicit stop proof; confirm it before asserting availability.
    const blockedContinue = screen.getByRole('button', { name: 'Continue existing work' });
    expect(document.getElementById(blockedContinue.getAttribute('aria-describedby') || '')?.textContent).toBe(
      'Continue existing work: Confirm prior writer stop proof first.',
    );
    const stopProof = screen.getByRole('checkbox') as HTMLInputElement;
    fireEvent.click(stopProof);
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Continue existing work' }) as HTMLButtonElement).disabled).toBe(false),
    );
    const resolve = screen.getByRole('button', { name: 'Resolve competing-attempt conflict' }) as HTMLButtonElement;
    expect(resolve.disabled).toBe(true);
    // The server's disabled reason is visible text linked to the button, not only a tooltip.
    const resolveReason = document.getElementById(resolve.getAttribute('aria-describedby') || '');
    expect(resolveReason?.textContent).toBe('Resolve competing-attempt conflict: no competing refs');
    expect(screen.getByText('Resolve competing-attempt conflict: no competing refs')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Continue existing work' }).getAttribute('aria-describedby')).toBeNull();
    // Prior failure history is preserved and visible.
    expect(screen.getByText(/Prior failed attempts preserved: 1/)).not.toBeNull();
    // Advanced evidence is progressively disclosed behind a closed
    // <details> element, not shown by default.
    const details = document.querySelector('[data-testid="issue-lifecycle-panel"] details');
    expect(details).not.toBeNull();
    expect((details as HTMLDetailsElement).open).toBe(false);
  });

  it('reports a failed action request as an error, not a server block', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).endsWith('/actions/submit')) {
          const body = JSON.stringify({ detail: { message: 'Lifecycle store unavailable' } });
          return {
            ok: false,
            status: 503,
            statusText: 'Service Unavailable',
            text: async () => body,
            json: async () => JSON.parse(body),
          };
        }
        return { ok: true, json: async () => PROJECTION };
      }) as unknown as typeof fetch,
    );
    renderWithClient(
      <IssueLifecyclePanel apiBase="/api" evidence={{ repository: 'o/r', issueNumber: 4183 }} />,
    );
    const hold = await screen.findByRole('button', { name: 'Hold processing' });
    fireEvent.click(hold);

    await waitFor(() => expect(screen.getByText(/Submission failed/)).not.toBeNull());
    expect(screen.getByText(/Lifecycle store unavailable/)).not.toBeNull();
    expect(screen.queryByText(/Blocked by the server/)).toBeNull();
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
