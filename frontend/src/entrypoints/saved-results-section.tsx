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
  projectSavedResults,
  publishSavedWork,
  savedResultContinuationKey,
  savedWorkPublicationIdentity,
  type SavedResultArtifactLike,
  type SavedResultEntry,
  type SavedResultExecutionLike,
  type SavedWorkDestinationDraft,
  type SavedWorkPartEntry,
  type SavedWorkPublicationRequest,
  type SavedWorkPublicationResult,
} from './saved-results';

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
  const [publishTarget, setPublishTarget] = useState<string | null>(null);
  const [draft, setDraft] = useState<SavedWorkDestinationDraft>(() =>
    defaultSavedWorkDestination(sourceExecution, workflowId),
  );
  const [publishOp, setPublishOp] = useState<PublishOperation | null>(null);

  useEffect(() => {
    continueRequest.current += 1;
    publishRequest.current += 1;
    continueInFlight.current = false;
    publishInFlight.current = false;
    setContinueOpen(false);
    setContinueOp(null);
    setPublishTarget(null);
    setPublishOp(null);
  }, [projection.selectedKey]);

  const terminalSource = projection.terminalSource;
  const continueDisabledReason = !actionsEnabled
    ? 'Workflow actions are disabled.'
    : !terminalSource
      ? 'Continue is available once the source run is terminal.'
      : capturedEvidenceQuery.isLoading
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
        if (admissionChanged(err)) onRefresh();
      } else {
        setContinueOp({ status: 'uncertain', error: errorMessage(err) });
      }
    } finally {
      if (token === continueRequest.current) continueInFlight.current = false;
    }
  };

  const openPublish = (savedWorkRef: string) => {
    if (publishTarget !== savedWorkRef) {
      setDraft(defaultSavedWorkDestination(sourceExecution, workflowId));
    }
    setPublishTarget(savedWorkRef);
  };

  const submitPublish = async (event: FormEvent) => {
    event.preventDefault();
    if (!publishTarget || publishInFlight.current) {
      return;
    }
    const request = buildSavedWorkPublicationRequest(publishTarget, draft);
    if (!request.destination.repository || !request.destination.headBranch) {
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
      if (err instanceof SavedWorkPublicationError) {
        setPublishOp({ status: 'failed', request, error: errorMessage(err) });
        if (admissionChanged(err)) onRefresh();
      } else {
        setPublishOp({ status: 'uncertain', request, error: errorMessage(err) });
      }
    } finally {
      if (token === publishRequest.current) publishInFlight.current = false;
    }
  };

  const currentPublishIdentity = publishTarget
    ? savedWorkPublicationIdentity(buildSavedWorkPublicationRequest(publishTarget, draft))
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
    setDraft((current) => ({ ...current, ...patch }));

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
                              disabled={!actionsEnabled || !publishable}
                              title={
                                !actionsEnabled
                                  ? 'Workflow actions are disabled.'
                                  : publishable
                                    ? undefined
                                    : 'Only complete, unexpired saved work with raw access can be published.'
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
              <label className="field">
                <span className="small">Publish as</span>
                <select
                  value={draft.objective}
                  onChange={(event) =>
                    updateDraft({
                      objective: event.target.value as SavedWorkDestinationDraft['objective'],
                    })
                  }
                >
                  <option value="pr">Pull request</option>
                  <option value="draft_pr">Draft pull request</option>
                  <option value="branch">Branch only</option>
                </select>
              </label>
              <label className="field">
                <span className="small">Base branch</span>
                <input
                  type="text"
                  value={draft.baseBranch}
                  onChange={(event) => updateDraft({ baseBranch: event.target.value })}
                />
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
                  <option value="baseline_delta">Recorded delta onto the saved baseline</option>
                  <option value="additive_import">Additive import</option>
                  <option value="empty_initialization">Initialize an empty destination</option>
                </select>
              </label>
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
                    publishPending || !draft.repository.trim() || !draft.headBranch.trim()
                  }
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
                  disabled={continuePending || !continueInstructions.trim()}
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
