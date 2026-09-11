/**
 * GitHub issue recovery-status projection for Workflow Detail (#4183).
 *
 * Pure projection of the server-derived lifecycle context returned by
 * `POST /api/v1/executions/issue-lifecycle/context`. This component owns
 * no lifecycle state engine: every lineage fact, attention category,
 * availability explanation, and action availability flag is rendered
 * from the server response. Advanced evidence is progressively disclosed
 * behind a native `<details>` element so the normal interface stays
 * streamlined.
 *
 * Design section 9; dashboard tokens via shared `dashboard.css` classes
 * (`stack`, `notice`, `small`, `segmented-control`); accessible action
 * patterns (real buttons, aria-live result regions).
 */

import { useEffect, useMemo, useState } from 'react';

export type IssueLifecycleEvidence = {
  repository?: string | undefined;
  issueNumber?: number | undefined;
  issue?: Record<string, unknown> | undefined;
  currentAttempt?: Record<string, unknown> | null | undefined;
  predecessorAttempts?: Array<Record<string, unknown>> | undefined;
  preservedPr?: Record<string, unknown> | null | undefined;
  retryState?: Record<string, unknown> | null | undefined;
  operatorHold?: Record<string, unknown> | null | undefined;
  syncState?: Record<string, unknown> | null | undefined;
  localFacts?: Record<string, unknown> | null | undefined;
};

type LifecycleAction = {
  action: string;
  enabled: boolean;
  disabled_reason?: string;
  continue_variant?: string;
};

type LifecycleResponse = {
  context: Record<string, unknown>;
  attention_category: string | null;
  recovery_availability: { available: boolean; reason: string; detail: string };
  actions: LifecycleAction[];
  continue_variants: string[];
};

const ATTENTION_LABELS: Record<string, string> = {
  confirmed_local_failure: 'Confirmed local failure',
  unresponsive_remote_owner: 'Unresponsive remote owner',
  deliberate_cancellation_hold: 'Deliberate cancellation / hold',
  exhausted_retries: 'Exhausted retries',
  unknown_publication_result: 'Unknown publication result',
  private_only_saved_work: 'Private-only saved work',
};

const ACTION_LABELS: Record<string, string> = {
  continue_work: 'Continue existing work',
  hold_processing: 'Hold processing',
  acknowledge_incident: 'Acknowledge incident',
  resolve_conflict: 'Resolve competing-attempt conflict',
  authorize_retry: 'Authorize retry',
  abandon_work: 'Abandon preserved work',
};

function asString(value: unknown): string {
  return typeof value === 'string' ? value : value === null || value === undefined ? '' : String(value);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * Derive issue-lifecycle evidence from a Workflow Detail execution object.
 *
 * Reads the portable `github_issue` shapes carried in input parameters or
 * memo (as admitted by the Search and Implement preset family). Returns an
 * empty evidence object when no issue is linked rather than fabricating
 * ownership: the panel then renders an honest empty state.
 */
export function issueLifecycleEvidenceFromExecution(execution: unknown): IssueLifecycleEvidence {
  const root = asRecord(execution) ?? {};
  const inputParameters = asRecord(root.inputParameters ?? root.input_parameters) ?? {};
  const memo = asRecord(root.memo) ?? {};
  const candidate =
    asRecord(inputParameters.github_issue ?? inputParameters.githubIssue) ??
    asRecord(memo.github_issue_lifecycle ?? memo.githubIssueLifecycle) ??
    null;
  if (!candidate) {
    const repository =
      asString(inputParameters.repository || memo.repository || (asRecord(inputParameters.github) ?? {}).repository);
    const rawNumber =
      inputParameters.github_issue_number ??
      inputParameters.githubIssueNumber ??
      inputParameters.issue_number ??
      memo.github_issue_number ??
      null;
    const issueNumber = typeof rawNumber === 'number' ? rawNumber : Number.parseInt(asString(rawNumber), 10);
    if (!repository || !Number.isFinite(issueNumber)) return {};
    return { repository, issueNumber };
  }
  const repository = asString(candidate.repository || (asRecord(candidate.issue) ?? {}).repository);
  const rawNumber = candidate.issue_number ?? candidate.issueNumber ?? candidate.number;
  const issueNumber = typeof rawNumber === 'number' ? rawNumber : Number.parseInt(asString(rawNumber), 10);
  if (!repository || !Number.isFinite(issueNumber)) return {};
  const predecessors = Array.isArray(candidate.predecessor_attempts ?? candidate.predecessorAttempts)
    ? ((candidate.predecessor_attempts ?? candidate.predecessorAttempts) as Array<Record<string, unknown>>)
    : [];
  return {
    repository,
    issueNumber,
    issue: asRecord(candidate.issue) ?? undefined,
    currentAttempt: asRecord(candidate.current_attempt ?? candidate.currentAttempt) ?? null,
    predecessorAttempts: predecessors,
    preservedPr: asRecord(candidate.preserved_pr ?? candidate.preservedPr) ?? null,
    retryState: asRecord(candidate.retry_state ?? candidate.retryState) ?? null,
    operatorHold: asRecord(candidate.operator_hold ?? candidate.operatorHold) ?? null,
    syncState: asRecord(candidate.sync_state ?? candidate.syncState) ?? null,
    localFacts: asRecord(candidate.local_facts ?? candidate.localFacts) ?? null,
  };
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((entry) => asString(entry)).filter(Boolean) : [];
}

export function IssueLifecyclePanel({
  apiBase,
  evidence,
}: {
  apiBase: string;
  evidence: IssueLifecycleEvidence;
}) {
  const repository = (evidence.repository || '').trim();
  const issueNumber = evidence.issueNumber;
  const [data, setData] = useState<LifecycleResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [continueVariant, setContinueVariant] = useState<string>('');
  const [actionResult, setActionResult] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<string | null>(null);

  const requestBody = useMemo(
    () =>
      JSON.stringify({
        repository,
        issue_number: issueNumber,
        issue: evidence.issue ?? null,
        current_attempt: evidence.currentAttempt ?? null,
        predecessor_attempts: evidence.predecessorAttempts ?? [],
        preserved_pr: evidence.preservedPr ?? null,
        retry_state: evidence.retryState ?? null,
        operator_hold: evidence.operatorHold ?? null,
        sync_state: evidence.syncState ?? null,
        local_facts: evidence.localFacts ?? null,
      }),
    [
      repository,
      issueNumber,
      evidence.issue,
      evidence.currentAttempt,
      evidence.predecessorAttempts,
      evidence.preservedPr,
      evidence.retryState,
      evidence.operatorHold,
      evidence.syncState,
      evidence.localFacts,
    ],
  );

  useEffect(() => {
    if (!repository || !issueNumber) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${apiBase}/api/v1/executions/issue-lifecycle/context`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: requestBody,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Lifecycle projection request failed (${response.status}).`);
        return (await response.json()) as LifecycleResponse;
      })
      .then((payload) => {
        if (cancelled) return;
        setData(payload);
        const suggested = payload.actions.find((entry) => entry.action === 'continue_work')?.continue_variant;
        if (suggested) setContinueVariant(suggested);
      })
      .catch((fetchError: Error) => {
        if (!cancelled) setError(fetchError.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiBase, repository, issueNumber, requestBody]);

  if (!repository || !issueNumber) {
    return (
      <section className="stack" aria-label="Issue lifecycle" data-testid="issue-lifecycle-empty">
        <h3>Issue lifecycle</h3>
        <p className="small">
          No GitHub issue is linked to this execution. Recovery status appears here once the
          execution carries validated issue evidence.
        </p>
      </section>
    );
  }

  const context = (data?.context ?? {}) as Record<string, unknown>;
  const availability = data?.recovery_availability;
  const attention = data?.attention_category;
  const actions = data?.actions ?? [];
  const variants = data?.continue_variants ?? [];

  async function submitAction(action: string) {
    setSubmitting(action);
    setActionResult(null);
    try {
      const response = await fetch(`${apiBase}/api/v1/executions/issue-lifecycle/actions/submit`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          action,
          repository,
          issue_number: issueNumber,
          request: {
            issue_number: issueNumber,
            idempotency_key:
              typeof crypto !== 'undefined' && 'randomUUID' in crypto
                ? crypto.randomUUID()
                : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
          },
          live_issue: evidence.issue ?? null,
          live_attempt: evidence.currentAttempt ?? null,
          live_pr: evidence.preservedPr ?? null,
          reason: '',
          continue_variant: action === 'continue_work' ? continueVariant || undefined : undefined,
        }),
      });
      const payload = (await response.json()) as {
        allowed: boolean;
        verdict?: { message?: string };
        decision?: unknown;
        publication?: { status?: string; detail?: string; commentId?: unknown } | null;
      };
      if (!payload.allowed) {
        setActionResult(`Blocked by the server: ${asString(payload.verdict?.message) || action}.`);
        return;
      }
      const publication = payload.publication;
      const publicationNote = publication
        ? publication.status === 'published'
          ? ` Published as GitHub comment ${asString(publication.commentId) || 'created'}.`
          : ` Publication ${asString(publication.status) || 'unknown'}: ${asString(publication.detail) || asString(publication.status) || 'see server detail'}.`
        : '';
      setActionResult(
        `Accepted and recorded: ${asString(payload.verdict?.message) || action}.${publicationNote}`,
      );
    } catch (submitError) {
      setActionResult(`Submission failed: ${submitError instanceof Error ? submitError.message : String(submitError)}.`);
    } finally {
      setSubmitting(null);
    }
  }

  return (
    <section className="stack" aria-label="Issue lifecycle" data-testid="issue-lifecycle-panel">
      <div>
        <h3>Issue lifecycle</h3>
        <p className="small">
          Server-derived projection of validated GitHub evidence and local execution facts — not a
          cross-device ownership database.
        </p>
      </div>
      {loading ? <p className="small">Loading recovery status…</p> : null}
      {error ? (
        <div className="notice error" role="alert">
          Recovery status is pending/unknown: {error}
        </div>
      ) : null}
      {data ? (
        <>
          <dl className="issue-lifecycle-facts">
            <div>
              <dt>Issue</dt>
              <dd>
                <a href={asString(context.issue_url) || '#'} rel="noreferrer" target="_blank">
                  {asString(context.issue_ref) || `${repository}#${issueNumber}`}
                </a>{' '}
                ({asString(context.issue_state) || 'unknown'} · {asString(context.settled_lifecycle_state) || 'unknown'})
              </dd>
            </div>
            <div>
              <dt>Attempt lineage</dt>
              <dd>
                {asString((context.current_attempt as Record<string, unknown> | null)?.['attempt_id'] as string) || 'current attempt unknown'}
                {' '}· {(context.predecessor_attempts as unknown[] | undefined)?.length ?? 0} predecessor(s)
                {' '}· deployment {asString(context.originating_deployment_id) || 'unknown'}
              </dd>
            </div>
            <div>
              <dt>Preserved work</dt>
              <dd>
                {asString((context.preserved_pr as Record<string, unknown> | null)?.['pr_url'] as string) || 'no preserved PR'}
                {asString(context.preserved_revision) ? ` @ ${asString(context.preserved_revision).slice(0, 12)}` : ''}
              </dd>
            </div>
            <div>
              <dt>Recovery</dt>
              <dd>
                {asString(context.recovery_phase) || 'unknown'} —{' '}
                {availability ? `${availability.available ? 'available' : 'blocked'}: ${availability.detail}` : 'evaluating…'}
              </dd>
            </div>
            {attention ? (
              <div>
                <dt>Needs attention</dt>
                <dd>{ATTENTION_LABELS[attention] ?? attention} — persists until explicitly resolved.</dd>
              </div>
            ) : null}
          </dl>
          {asList(context.remaining_requirements).length > 0 ? (
            <div>
              <h4>Remaining requirements</h4>
              <ul>
                {asList(context.remaining_requirements).map((requirement) => (
                  <li key={requirement}>{requirement}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {(context.prior_failure_count as number | undefined) ? (
            <p className="small">
              Prior failed attempts preserved: {String(context.prior_failure_count)}. A later launch
              does not erase this history.
            </p>
          ) : null}
          {variants.length > 0 && actions.some((entry) => entry.action === 'continue_work' && entry.enabled) ? (
            <fieldset className="segmented-control-field">
              <legend>Continue behavior</legend>
              <div className="segmented-control" data-intensity="quiet" role="radiogroup" aria-label="Continue behavior">
                {variants.map((variant) => (
                  <label key={variant} className="segmented-control-item">
                    <input
                      type="radio"
                      name="issue-lifecycle-continue-variant"
                      value={variant}
                      checked={(continueVariant || variants[0]) === variant}
                      onChange={() => setContinueVariant(variant)}
                    />
                    <span>{variant.replaceAll('_', ' ')}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          ) : null}
          <div className="issue-lifecycle-actions">
            {actions.map((entry) => (
              <button
                key={entry.action}
                type="button"
                className="secondary"
                disabled={!entry.enabled || submitting !== null}
                title={entry.enabled ? undefined : entry.disabled_reason || 'Unavailable'}
                onClick={() => void submitAction(entry.action)}
                aria-label={ACTION_LABELS[entry.action] ?? entry.action}
              >
                {submitting === entry.action ? 'Submitting…' : (ACTION_LABELS[entry.action] ?? entry.action)}
              </button>
            ))}
          </div>
          {actionResult ? (
            <div className="notice" role="status" aria-live="polite">
              {actionResult}
            </div>
          ) : null}
          <details>
            <summary>Advanced evidence</summary>
            <dl className="issue-lifecycle-facts">
              <div>
                <dt>Evidence freshness / completeness</dt>
                <dd>
                  {asString(context.evidence_freshness) || 'unknown'} / {asString(context.evidence_completeness) || 'unknown'}
                  {' '}· sync {asString(context.sync_status) || 'unknown'}
                </dd>
              </div>
              <div>
                <dt>Retry allowance / cooldown</dt>
                <dd>
                  {asString(context.retry_allowance_remaining) || 'unknown'}
                  {asString(context.retry_cooldown_until) ? ` · cooldown until ${asString(context.retry_cooldown_until)}` : ''}
                  {asString(context.retry_block_reason) ? ` · ${asString(context.retry_block_reason)}` : ''}
                </dd>
              </div>
              <div>
                <dt>Pending sync errors</dt>
                <dd>{asList(context.pending_sync_errors).join('; ') || 'none'}</dd>
              </div>
              <div>
                <dt>Competing refs preserved</dt>
                <dd>{asList(context.competing_refs_preserved).join(', ') || 'none'}</dd>
              </div>
            </dl>
          </details>
        </>
      ) : null}
    </section>
  );
}
