import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';

import {
  ContinuationRequestError,
  continuationWorkflowHref,
  continueInNewWorkflow,
  fetchCapturedEvidence,
} from '../features/workflow-native-chat/WorkflowTerminalChatActions';
import { formatStatusLabel } from '../utils/formatters';
import {
  SavedWorkPublicationError,
  buildSavedResultDownloadHref,
  buildSavedResultPreviewHref,
  buildSavedWorkPublicationRequest,
  canDownloadSavedResultRaw,
  canPublishSavedWork,
  defaultSavedWorkDestination,
  fetchSavedWorkPublicationOperation,
  newSavedWorkAdmissionGeneration,
  projectSavedResults,
  publishSavedWork,
  savedResultContinuationKey,
  savedWorkPublicationAvailability,
  savedWorkPublicationIdentity,
  savedWorkPublicationUnavailableMessage,
  type SavedResultArtifactLike,
  type SavedResultEntry,
  type SavedResultExecutionLike,
  type SavedWorkDestinationDraft,
  type SavedWorkObjective,
  type SavedWorkPartEntry,
  type SavedWorkPublicationOperation,
  type SavedWorkPublicationRequest,
  type SavedWorkPublicationResult,
  type SavedWorkStrategy,
} from './saved-results';

const PUBLICATION_FOLLOW_INTERVAL_MS = 5_000;

const OBJECTIVE_LABELS: Record<SavedWorkObjective, string> = {
  pr: 'Pull request',
  draft_pr: 'Draft pull request',
  branch: 'Branch only',
};

const STRATEGY_LABELS: Record<SavedWorkStrategy, string> = {
  baseline_delta: 'Recorded delta onto the saved baseline',
  additive_import: 'Additive import',
  empty_initialization: 'Initialize an empty destination',
};

type ContinueOperation = {
  status: 'pending' | 'admitted' | 'failed' | 'uncertain';
  destinationWorkflowId?: string;
  created?: boolean;
  error?: string;
};

type PublishOperation = {
  status: 'pending' | 'started' | 'failed' | 'uncertain';
  request: SavedWorkPublicationRequest;
  result?: SavedWorkPublicationResult;
  error?: string;
};

function Card({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="card">
      <strong>{label}:</strong> <span className="break-words">{children}</span>
    </div>
  );
}

function admissionChanged(error: unknown): boolean {
  const status =
    error instanceof ContinuationRequestError || error instanceof SavedWorkPublicationError
      ? error.status
      : 0;
  return status === 401 || status === 403;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function destinationSummary(request: SavedWorkPublicationRequest): string {
  const { destination } = request;
  const base = destination.baseBranch ? ` from ${destination.baseBranch}` : '';
  return `${destination.repository} ${destination.headBranch}${base} (${formatStatusLabel(
    destination.objective,
  )}, ${formatStatusLabel(destination.strategy)})`;
}

// Simple destination choices come first. The base and how saved work is
// applied are shown when an existing destination still needs a base or a
// non-default application is chosen.
function needsAdvancedDestination(draft: SavedWorkDestinationDraft): boolean {
  const baseMissing = draft.strategy !== 'empty_initialization' && !draft.baseBranch.trim();
  return baseMissing || draft.strategy !== 'baseline_delta';
}

function publicationOutcomeText(operation: SavedWorkPublicationOperation): string {
  if (!operation.terminal) {
    return operation.outcome === 'unknown'
      ? 'The publication run has not reported its status yet; following it until it finishes.'
      : `Publication run ${formatStatusLabel(operation.outcome).toLowerCase()}; following it until it finishes.`;
  }
  if (operation.outcome === 'completed') {
    return 'Publication completed: the saved work was published, or the destination already had it. The run records the result.';
  }
  if (operation.outcome === 'canceled') {
    return 'Publication canceled. The run records any change it had already confirmed.';
  }
  return `Publication ${formatStatusLabel(operation.outcome).toLowerCase()}. The run records the reason; the saved work is unchanged.`;
}

function SavedWorkParts({
  apiBase,
  parts,
  artifacts,
}: {
  apiBase: string;
  parts: SavedWorkPartEntry[];
  artifacts: SavedResultArtifactLike[];
}) {
  if (parts.length === 0) {
    return null;
  }
  return (
    <ul className="small td-saved-work-parts" aria-label="Saved work parts">
      {parts.map((part) => {
        const source = artifacts.find((item) => item.artifactId === part.artifactId);
        return (
          <li key={`${part.role}-${part.artifactId}`}>
            {formatStatusLabel(part.role)} <code>{part.artifactId}</code>{' '}
            {part.complete ? 'complete' : `incomplete (${part.completenessReason})`}
            {part.expired ? ' · expired' : ''}{' '}
            {source && part.present && !part.expired && canDownloadSavedResultRaw(source) ? (
              <a
                className="button secondary"
                href={buildSavedResultDownloadHref(apiBase, source)}
                title={`Download ${part.role}`}
              >
                Download
              </a>
            ) : (
              <span>{part.present ? 'Raw unavailable' : 'Not listed'}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function SavedWorkDetails({ entry }: { entry: SavedResultEntry }) {
  const unit = entry.savedWork;
  if (!unit) {
    return null;
  }
  if (!unit.summaryAvailable) {
    return <div className="small">Format details are unavailable for this saved work.</div>;
  }
  return (
    <div className="small stack">
      {unit.formats.length > 0 ? (
        <div>
          Formats:{' '}
          {unit.formats
            .map(
              (format) =>
                `${formatStatusLabel(format.format)}${format.required ? ' (required)' : ''}: ${formatStatusLabel(format.status)}`,
            )
            .join(' · ')}
        </div>
      ) : null}
      {unit.exclusionReasons.length > 0 ? (
        <div>
          Excluded by{' '}
          {unit.exclusionReasons
            .map((item) => `${formatStatusLabel(item.reason)} (${item.count})`)
            .join(', ')}
        </div>
      ) : null}
      {unit.limitations.map((limitation) => (
        <div key={limitation}>Limitation: {limitation}</div>
      ))}
    </div>
  );
}

export function SavedResultsSection({
  workflowId,
  runId,
  apiBase,
  execution,
  artifacts,
  isLoading,
  error,
  stale,
  onRefresh,
  actionsEnabled,
}: {
  workflowId: string;
  runId: string;
  apiBase: string;
  execution: SavedResultExecutionLike | null | undefined;
  artifacts: SavedResultArtifactLike[];
  isLoading: boolean;
  error: Error | null;
  stale: boolean;
  onRefresh: () => void;
  actionsEnabled: boolean;
}) {
  // `/continue` authorizes only the source's captured-evidence refs, so only
  // those saved outputs are submitted with a continuation.
  const capturedEvidenceQuery = useQuery({
    queryKey: ['workflow-captured-evidence', workflowId],
    queryFn: () => fetchCapturedEvidence(apiBase, workflowId),
    enabled: Boolean(workflowId),
    staleTime: 60_000,
    retry: false,
  });
  const capturedEvidence = capturedEvidenceQuery.data;
  const authorizedContinuationRefs = useMemo(
    () =>
      capturedEvidence?.available
        ? capturedEvidence.items.map((item) => item.artifactRef)
        : [],
    [capturedEvidence],
  );
  const sourceExecution = useMemo<SavedResultExecutionLike>(
    () => execution ?? { workflowId, runId, state: 'unknown' },
    [execution, workflowId, runId],
  );
  const projection = useMemo(
    () =>
      projectSavedResults({
        workflowId,
        runId,
        execution: sourceExecution,
        artifacts,
        artifactsStale: stale,
        artifactsError: error,
        authorizedContinuationRefs,
      }),
    [workflowId, runId, sourceExecution, artifacts, stale, error, authorizedContinuationRefs],
  );

  // Requests and displayed state belong to one selected result. A response
  // applies only to the selection and the latest request that produced it, so
  // a late answer never retargets a historical selection or overwrites a
  // newer operation.
  const selectionKeyRef = useRef(projection.selectedKey);
  selectionKeyRef.current = projection.selectedKey;
  const continueRequest = useRef(0);
  const publishRequest = useRef(0);
  const continueInFlight = useRef(false);
  const publishInFlight = useRef(false);
  const [continueOpen, setContinueOpen] = useState(false);
  const [continueTitle, setContinueTitle] = useState('');
  const [continueInstructions, setContinueInstructions] = useState('');
  const [continueOp, setContinueOp] = useState<ContinueOperation | null>(null);
  const publication = useMemo(
    () => savedWorkPublicationAvailability(sourceExecution),
    [sourceExecution],
  );
  const [publishTarget, setPublishTarget] = useState<string | null>(null);
  const [draft, setDraft] = useState<SavedWorkDestinationDraft>(() =>
    defaultSavedWorkDestination(sourceExecution, workflowId, publication.allowedModes),
  );
  const [advancedRequested, setAdvancedRequested] = useState(false);
  const [publishOp, setPublishOp] = useState<PublishOperation | null>(null);

  useEffect(() => {
    continueRequest.current += 1;
    publishRequest.current += 1;
    continueInFlight.current = false;
    publishInFlight.current = false;
    setContinueOpen(false);
    setContinueOp(null);
    setPublishTarget(null);
    setAdvancedRequested(false);
    setPublishOp(null);
  }, [projection.selectedKey]);

  // An accepted publication is followed through its own execution read until
  // it finishes; closing the form or changing the destination does not stop it.
  const followedPublication =
    publishOp?.status === 'started' && publishOp.result ? publishOp.result.workflowId : null;
  const publicationOperationQuery = useQuery({
    queryKey: ['saved-work-publication-operation', followedPublication],
    queryFn: () => fetchSavedWorkPublicationOperation(apiBase, followedPublication ?? ''),
    enabled: Boolean(followedPublication),
    refetchInterval: (query) =>
      query.state.data?.terminal ? false : PUBLICATION_FOLLOW_INTERVAL_MS,
    retry: false,
  });

  const terminalSource = projection.terminalSource;
  const continueDisabledReason = !actionsEnabled
    ? 'Workflow actions are disabled.'
    : !terminalSource
      ? 'Continue is available once the source run is terminal.'
      : capturedEvidenceQuery.isFetching
        ? 'Loading the evidence this continuation may carry.'
        : null;

  const submitContinue = async (event: FormEvent) => {
    event.preventDefault();
    const instructions = continueInstructions.trim();
    if (!instructions || continueInFlight.current || continueDisabledReason) {
      return;
    }
    const title = continueTitle.trim();
    const refs = [...projection.continuationRefs];
    const selection = selectionKeyRef.current;
    const token = (continueRequest.current += 1);
    const applies = () =>
      token === continueRequest.current && selection === selectionKeyRef.current;
    continueInFlight.current = true;
    setContinueOp({ status: 'pending' });
    try {
      const result = await continueInNewWorkflow(apiBase, workflowId, {
        idempotencyKey: savedResultContinuationKey(workflowId, runId, {
          instructions,
          title,
          selectedSourceArtifactRefs: refs,
        }),
        instructions,
        ...(title ? { title } : {}),
        selectedSourceArtifactRefs: refs,
      });
      if (!applies()) return;
      setContinueOp({
        status: 'admitted',
        destinationWorkflowId: result.destinationWorkflowId,
        created: result.created,
      });
    } catch (err) {
      if (!applies()) return;
      if (err instanceof ContinuationRequestError) {
        setContinueOp({ status: 'failed', error: errorMessage(err) });
        if (admissionChanged(err)) {
          void capturedEvidenceQuery.refetch();
          onRefresh();
        }
      } else {
        setContinueOp({ status: 'uncertain', error: errorMessage(err) });
      }
    } finally {
      if (token === continueRequest.current) continueInFlight.current = false;
    }
  };

  const openPublish = (savedWorkRef: string) => {
    if (publishTarget !== savedWorkRef) {
      const next = defaultSavedWorkDestination(
        sourceExecution,
        workflowId,
        publication.allowedModes,
      );
      setDraft(next);
      // Stay open once shown, so filling in the base does not hide it.
      setAdvancedRequested(needsAdvancedDestination(next));
    }
    setPublishTarget(savedWorkRef);
  };

  const submitPublish = async (event: FormEvent) => {
    event.preventDefault();
    if (!publishTarget || publishInFlight.current || !publication.available) {
      return;
    }
    const request: SavedWorkPublicationRequest = {
      ...buildSavedWorkPublicationRequest(publishTarget, draft),
      sourceRunId: runId,
    };
    if (!request.destination.repository || !request.destination.headBranch) {
      return;
    }
    // Keep uncertain admissions across reloads and selection changes. Only
    // an explicit submission after matching terminal proof starts a new one.
    const storageKey = `moonmind.savedWorkPublication:${JSON.stringify([
      apiBase, workflowId, savedWorkPublicationIdentity(request),
    ])}`;
    const knownTerminal =
      publishOp?.status === 'started' &&
      publicationOperationQuery.data?.terminal &&
      publicationOperationQuery.data.workflowId === publishOp.result?.workflowId &&
      savedWorkPublicationIdentity(publishOp.request) === savedWorkPublicationIdentity(request);
    try {
      const retained = window.sessionStorage.getItem(storageKey);
      const generation = knownTerminal && retained === publishOp.request.admissionGeneration
        ? null : retained;
      request.admissionGeneration = generation || newSavedWorkAdmissionGeneration();
      // Store before POST: a lost acknowledgment must remain reconcilable
      // after reload, including after a subsequent session/policy refusal.
      window.sessionStorage.setItem(storageKey, request.admissionGeneration);
    } catch {
      setPublishOp({
        status: 'failed', request,
        error: 'Publication could not start because this browser could not retain its request. Allow site storage and retry.',
      });
      return;
    }
    const selection = selectionKeyRef.current;
    const token = (publishRequest.current += 1);
    const applies = () =>
      token === publishRequest.current && selection === selectionKeyRef.current;
    publishInFlight.current = true;
    setPublishOp({ status: 'pending', request });
    try {
      const result = await publishSavedWork(apiBase, workflowId, request);
      if (!applies()) return;
      setPublishOp({ status: 'started', request, result });
    } catch (err) {
      if (!applies()) return;
      if (
        err instanceof SavedWorkPublicationError &&
        err.status < 500 &&
        err.status !== 408
      ) {
        setPublishOp({ status: 'failed', request, error: errorMessage(err) });
        // A policy refusal means the projected availability is out of date.
        if (admissionChanged(err) || err.code === 'publication_retry_not_admitted') onRefresh();
      } else {
        setPublishOp({ status: 'uncertain', request, error: errorMessage(err) });
      }
    } finally {
      if (token === publishRequest.current) publishInFlight.current = false;
    }
  };

  const currentPublishIdentity = publishTarget
    ? savedWorkPublicationIdentity({
        ...buildSavedWorkPublicationRequest(publishTarget, draft),
        sourceRunId: runId,
      })
    : null;
  const destinationChanged = Boolean(
    publishOp &&
      currentPublishIdentity &&
      publishOp.status !== 'pending' &&
      savedWorkPublicationIdentity(publishOp.request) !== currentPublishIdentity,
  );
  const publishPending = publishOp?.status === 'pending';
  const continuePending = continueOp?.status === 'pending';
  const updateDraft = (patch: Partial<SavedWorkDestinationDraft>) =>
    setDraft((current) => {
      const next = { ...current, ...patch };
      return next.strategy === 'empty_initialization'
        ? { ...next, objective: 'branch', baseBranch: '' }
        : next;
    });
  const publicationUnavailable = publication.available
    ? null
    : savedWorkPublicationUnavailableMessage(publication.reason);
  const hasSavedWork = projection.entries.some((entry) => entry.savedWork);
  const initializesEmpty = draft.strategy === 'empty_initialization';
  const baseMissing = !initializesEmpty && !draft.baseBranch.trim();
  const advancedVisible = advancedRequested || needsAdvancedDestination(draft);

  return (
    <section className="stack td-saved-results-region td-evidence-region">
      <div className="step-tl-section-header">
        <h3>Saved Results</h3>
        <span className="step-tl-count">
          Selected run {runId || '—'} | {projection.entries.length} saved
          output{projection.entries.length === 1 ? '' : 's'}
        </span>
      </div>
      <p className="small">
        Server-selected result with committed artifact references. Compute,
        saving, requested publication, and cleanup stay independent: a failed
        run may still have saved work, and pending cleanup never erases it.
      </p>
      {isLoading ? (
        <p className="loading">Loading saved results...</p>
      ) : projection.state === 'unavailable' ? (
        <div className="stack">
          <div className="notice">
            Saved result evidence is unavailable for this selection.
            {error ? ` ${error.message}` : ''}
          </div>
          <div className="actions">
            <button type="button" className="secondary" onClick={onRefresh}>
              Refresh
            </button>
          </div>
        </div>
      ) : projection.state === 'pending' ? (
        <div className="stack">
          <p className="small">Saved result evidence is pending.</p>
          <div className="actions">
            <button type="button" className="secondary" onClick={onRefresh}>
              Refresh
            </button>
          </div>
        </div>
      ) : projection.state === 'stale' ? (
        <div className="stack">
          <div className="notice">Saved result evidence is stale.</div>
          <div className="actions">
            <button type="button" className="secondary" onClick={onRefresh}>
              Refresh
            </button>
          </div>
        </div>
      ) : projection.entries.length === 0 ? (
        <p className="small">No saved report, non-Git, or repository output.</p>
      ) : (
        <>
          <div className="grid-2">
            <Card label="Compute">{formatStatusLabel(projection.computeOutcome)}</Card>
            <Card label="Save">{formatStatusLabel(projection.saveOutcome)}</Card>
            <Card label="Publication">
              {formatStatusLabel(projection.publicationOutcome)}
            </Card>
            <Card label="Cleanup">
              {formatStatusLabel(projection.cleanupOutcome)}
            </Card>
          </div>
          {hasSavedWork && actionsEnabled && publicationUnavailable ? (
            <p className="small" role="note">
              Publish saved work is unavailable: {publicationUnavailable} Download,
              preview, and Continue working are not affected.
            </p>
          ) : null}
          <div className="queue-table-wrapper td-evidence-slab" data-layout="table">
            <table>
              <thead>
                <tr>
                  <th>Saved output</th>
                  <th>Completeness</th>
                  <th>Retention</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {projection.entries.map((entry) => {
                  const source = artifacts.find(
                    (item) => item.artifactId === entry.artifactId,
                  );
                  const rawAllowed = source ? canDownloadSavedResultRaw(source) : false;
                  const publishable = canPublishSavedWork(entry);
                  return (
                    <tr key={entry.artifactId}>
                      <td>
                        <code>{entry.title}</code>
                        <div className="small">
                          {formatStatusLabel(entry.kind)}
                          {entry.expired ? ' · expired' : ''}
                          {entry.restricted ? ' · restricted' : ''}
                        </div>
                        {entry.savedWork ? (
                          <div className="small">
                            Manifest <code>{entry.artifactId}</code>
                          </div>
                        ) : null}
                        <SavedWorkDetails entry={entry} />
                        {entry.savedWork ? (
                          <SavedWorkParts
                            apiBase={apiBase}
                            parts={entry.savedWork.parts}
                            artifacts={artifacts}
                          />
                        ) : null}
                      </td>
                      <td>
                        {entry.complete
                          ? 'Complete'
                          : `Incomplete (${entry.completenessReason})`}
                        {entry.exclusions !== null ? (
                          <div className="small">
                            {entry.exclusions} excluded
                          </div>
                        ) : null}
                      </td>
                      <td>{entry.retention ?? 'Unknown'}</td>
                      <td>
                        <div className="actions">
                          {source && !entry.expired ? (
                            <a
                              className="button secondary"
                              href={buildSavedResultPreviewHref(apiBase, source)}
                              title="Open safe preview"
                            >
                              Preview
                            </a>
                          ) : null}
                          {entry.expired ? (
                            <span className="small">Expired</span>
                          ) : source && rawAllowed ? (
                            <a
                              className="button secondary"
                              href={buildSavedResultDownloadHref(apiBase, source)}
                              title="Download saved output"
                            >
                              Download
                            </a>
                          ) : (
                            <span className="small">Raw unavailable</span>
                          )}
                          {entry.savedWork ? (
                            <button
                              type="button"
                              className="secondary"
                              disabled={!actionsEnabled || !publishable || !publication.available}
                              title={
                                !actionsEnabled
                                  ? 'Workflow actions are disabled.'
                                  : !publishable
                                    ? 'Only complete, unexpired saved work with raw access can be published.'
                                    : publicationUnavailable ?? undefined
                              }
                              aria-expanded={publishTarget === entry.artifactId}
                              onClick={() => openPublish(entry.artifactId)}
                            >
                              Publish saved work
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {publishTarget ? (
            <form
              className="stack td-saved-work-publish-form"
              aria-label="Publish saved work"
              onSubmit={submitPublish}
            >
              <p className="small">
                Publishes saved work <code>{publishTarget}</code> through the
                publication-only path: no model is rerun, and the destination
                is checked by the server when it admits the request.
              </p>
              <label className="field">
                <span className="small">Repository</span>
                <input
                  type="text"
                  required
                  value={draft.repository}
                  onChange={(event) => updateDraft({ repository: event.target.value })}
                />
              </label>
              {publication.canaryRepositories.length > 0 ? (
                <p className="small">
                  The publication rollout currently admits only{' '}
                  {publication.canaryRepositories.join(', ')}.
                </p>
              ) : null}
              <label className="field">
                <span className="small">Publish as</span>
                <select
                  value={draft.objective}
                  disabled={initializesEmpty}
                  onChange={(event) =>
                    updateDraft({
                      objective: event.target.value as SavedWorkDestinationDraft['objective'],
                    })
                  }
                >
                  {(publication.allowedModes.includes(draft.objective)
                    ? publication.allowedModes
                    : [draft.objective, ...publication.allowedModes]
                  ).map((mode) => (
                    <option key={mode} value={mode}>
                      {OBJECTIVE_LABELS[mode]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span className="small">Head branch</span>
                <input
                  type="text"
                  required
                  value={draft.headBranch}
                  onChange={(event) => updateDraft({ headBranch: event.target.value })}
                />
              </label>
              {advancedVisible ? (
                <fieldset className="stack" aria-label="Advanced destination options">
                  {initializesEmpty ? (
                    <p className="small">
                      Initializing an empty destination publishes one branch with
                      no base branch.
                    </p>
                  ) : (
                    <label className="field">
                      <span className="small">Base branch</span>
                      <input
                        type="text"
                        required
                        value={draft.baseBranch}
                        onChange={(event) => updateDraft({ baseBranch: event.target.value })}
                      />
                    </label>
                  )}
                  {baseMissing ? (
                    <p className="small">
                      Publishing onto an existing destination needs its base branch.
                    </p>
                  ) : null}
                  <label className="field">
                    <span className="small">Apply saved work as</span>
                    <select
                      value={draft.strategy}
                      onChange={(event) =>
                        updateDraft({
                          strategy: event.target.value as SavedWorkDestinationDraft['strategy'],
                        })
                      }
                    >
                      {(Object.keys(STRATEGY_LABELS) as SavedWorkStrategy[]).map((strategy) => (
                        <option
                          key={strategy}
                          value={strategy}
                          disabled={strategy === 'empty_initialization' && !publication.allowedModes.includes('branch')}
                        >
                          {STRATEGY_LABELS[strategy]}
                        </option>
                      ))}
                    </select>
                  </label>
                </fieldset>
              ) : (
                <p className="small">
                  Base branch <code>{draft.baseBranch.trim()}</code> ·{' '}
                  {STRATEGY_LABELS[draft.strategy]}.{' '}
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setAdvancedRequested(true)}
                  >
                    Change base or how saved work is applied
                  </button>
                </p>
              )}
              {draft.objective !== 'branch' ? (
                <label className="field">
                  <span className="small">Pull request title (optional)</span>
                  <input
                    type="text"
                    maxLength={256}
                    value={draft.pullRequestTitle}
                    onChange={(event) => updateDraft({ pullRequestTitle: event.target.value })}
                  />
                </label>
              ) : null}
              <div className="actions">
                <button
                  type="submit"
                  disabled={
                    publishPending ||
                    !publication.available ||
                    !draft.repository.trim() ||
                    !draft.headBranch.trim()
                  }
                  title={publicationUnavailable ?? undefined}
                >
                  {publishPending ? 'Publishing...' : 'Publish to this destination'}
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setPublishTarget(null)}
                >
                  Close
                </button>
              </div>
              {destinationChanged ? (
                <p className="small">
                  The destination changed since the last request; publishing
                  again is a new decision for the new destination.
                </p>
              ) : null}
            </form>
          ) : null}
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={Boolean(continueDisabledReason)}
              title={continueDisabledReason ?? undefined}
              aria-expanded={continueOpen}
              onClick={() => setContinueOpen((open) => !open)}
            >
              Continue working
            </button>
          </div>
          {continueOpen ? (
            <form
              className="stack td-saved-results-continue-form"
              aria-label="Continue working"
              onSubmit={submitContinue}
            >
              <p className="small">
                Starts a fresh admitted execution that carries{' '}
                {projection.continuationRefs.length} authorized saved
                output{projection.continuationRefs.length === 1 ? '' : 's'}.
                The source run, its session, and its host are never reused.
              </p>
              <label className="field">
                <span className="small">Title (optional)</span>
                <input
                  type="text"
                  maxLength={500}
                  value={continueTitle}
                  onChange={(event) => setContinueTitle(event.target.value)}
                />
              </label>
              <label className="field">
                <span className="small">New instructions</span>
                <textarea
                  required
                  rows={3}
                  value={continueInstructions}
                  onChange={(event) => setContinueInstructions(event.target.value)}
                />
              </label>
              <div className="actions">
                <button
                  type="submit"
                  disabled={continuePending || Boolean(continueDisabledReason) || !continueInstructions.trim()}
                >
                  {continuePending ? 'Starting continuation...' : 'Start continuation'}
                </button>
              </div>
            </form>
          ) : null}
          {continueOp?.status === 'admitted' && continueOp.destinationWorkflowId ? (
            <p className="small" role="status">
              Continuation admitted:{' '}
              <a href={continuationWorkflowHref(continueOp.destinationWorkflowId)}>
                {continueOp.destinationWorkflowId}
              </a>
              {continueOp.created === false ? ' (existing continuation reused).' : '.'}
            </p>
          ) : null}
          {continueOp?.status === 'uncertain' ? (
            <div className="notice" role="status">
              The continuation request may have been accepted ({continueOp.error}).
              Submitting the same instructions again reuses the same operation.
            </div>
          ) : null}
          {continueOp?.status === 'failed' ? (
            <div className="notice error">{continueOp.error}</div>
          ) : null}
          {publishOp?.status === 'started' && publishOp.result ? (
            <p className="small" role="status">
              Publication started for {destinationSummary(publishOp.request)}:{' '}
              <a href={continuationWorkflowHref(publishOp.result.workflowId)}>
                {publishOp.result.workflowId}
              </a>{' '}
              (operation <code>{publishOp.result.publicationIdempotencyKey}</code>).
            </p>
          ) : null}
          {followedPublication ? (
            <p className="small" role="status" aria-label="Publication outcome">
              {publicationOperationQuery.data
                ? publicationOutcomeText(publicationOperationQuery.data)
                : publicationOperationQuery.error
                  ? `Publication status is unavailable (${errorMessage(
                      publicationOperationQuery.error,
                    )}). The accepted publication continues; open its run for details.`
                  : 'Reading the publication run status.'}
            </p>
          ) : null}
          {publishOp?.status === 'uncertain' ? (
            <div className="notice" role="status">
              The publication request for {destinationSummary(publishOp.request)} may
              have been accepted ({publishOp.error}). Publishing the same saved work
              to the same destination again reuses the same operation.
            </div>
          ) : null}
          {publishOp?.status === 'failed' ? (
            <div className="notice error">{publishOp.error}</div>
          ) : null}
        </>
      )}
    </section>
  );
}
