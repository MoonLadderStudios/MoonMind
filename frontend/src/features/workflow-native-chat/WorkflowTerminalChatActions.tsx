/**
 * Workflow-scoped terminal Chat actions (MoonLadderStudios/MoonMind#3632,
 * child #3641).
 *
 * These controls sit outside the native Omnigent application because they
 * create/read MoonMind Workflow state.  They never post a native message or
 * invoke SubmitChatInstruction.  The server pins source identity and evidence;
 * the browser supplies only new continuation intent and an idempotency key.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';

export interface CapturedEvidenceItem {
  label: string;
  kind: string;
  artifactRef: string;
}

export interface CapturedEvidence {
  workflowId: string;
  runId?: string | null;
  available: boolean;
  items: CapturedEvidenceItem[];
  summary?: string | null;
  unavailableReason?: string | null;
}

export interface ContinueInNewWorkflowResult {
  sourceWorkflowId: string;
  sourceRunId: string;
  destinationWorkflowId: string;
  relationshipType: string;
  created: boolean;
}

export interface ContinuationIntent {
  idempotencyKey: string;
  instructions: string;
  title?: string;
}

function joinApiPath(apiBase: string, path: string): string {
  const base = (apiBase || '/api').replace(/\/+$/, '');
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${base}${suffix}`;
}

export function capturedEvidenceHref(
  apiBase: string,
  workflowId: string,
  artifactRef: string,
): string | null {
  const ref = (artifactRef || '').trim();
  if (!ref) return null;
  if (/^https?:\/\//i.test(ref)) return ref;
  if (!workflowId) return null;
  const base = joinApiPath(
    apiBase,
    `/executions/${encodeURIComponent(workflowId)}/captured-evidence/download`,
  );
  return `${base}?ref=${encodeURIComponent(ref)}`;
}

export async function fetchCapturedEvidence(
  apiBase: string,
  workflowId: string,
): Promise<CapturedEvidence> {
  const resp = await fetch(
    joinApiPath(
      apiBase,
      `/executions/${encodeURIComponent(workflowId)}/captured-evidence`,
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    throw new Error(`captured evidence request failed (${resp.status})`);
  }
  return (await resp.json()) as CapturedEvidence;
}

export async function continueInNewWorkflow(
  apiBase: string,
  workflowId: string,
  intent: ContinuationIntent,
): Promise<ContinueInNewWorkflowResult> {
  const body: Record<string, unknown> = {
    idempotencyKey: intent.idempotencyKey,
    instructions: intent.instructions,
  };
  if (intent.title) body.title = intent.title;
  const resp = await fetch(
    joinApiPath(apiBase, `/executions/${encodeURIComponent(workflowId)}/continue`),
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!resp.ok) {
    throw new Error(`continue request failed (${resp.status})`);
  }
  return (await resp.json()) as ContinueInNewWorkflowResult;
}

export function continuationWorkflowHref(workflowId: string): string {
  return `/workflows/${encodeURIComponent(workflowId)}?source=temporal`;
}

function newIdempotencyKey(): string {
  const cryptoObj =
    typeof globalThis !== 'undefined'
      ? (globalThis.crypto as Crypto | undefined)
      : undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') {
    return `continue:${cryptoObj.randomUUID()}`;
  }
  return `continue:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

function CapturedEvidencePanel({
  apiBase,
  workflowId,
}: {
  apiBase: string;
  workflowId: string;
}): React.ReactElement {
  const query = useQuery({
    queryKey: ['workflow-captured-evidence', workflowId],
    queryFn: () => fetchCapturedEvidence(apiBase, workflowId),
    enabled: Boolean(workflowId),
    staleTime: 60_000,
    retry: false,
  });
  const evidence = query.data ?? null;

  return (
    <div
      className="td-native-chat-evidence"
      data-testid="workflow-native-chat-evidence"
    >
      {query.isLoading ? (
        <p className="small">Loading captured evidence…</p>
      ) : query.isError ? (
        <p className="small" role="alert">
          Captured evidence is unavailable.
        </p>
      ) : evidence && evidence.available && evidence.items.length > 0 ? (
        <ul className="td-native-chat-evidence-list">
          {evidence.items.map((item, index) => {
            const href = capturedEvidenceHref(apiBase, workflowId, item.artifactRef);
            return (
              <li key={`${item.kind}-${index}`}>
                {href ? (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    data-testid="workflow-native-chat-evidence-link"
                  >
                    {item.label}
                  </a>
                ) : (
                  <span>{item.label}</span>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="small">
          {evidence?.unavailableReason
            ? `No captured evidence: ${evidence.unavailableReason}`
            : 'No captured evidence is available for this workflow.'}
        </p>
      )}
    </div>
  );
}

export function WorkflowTerminalChatActions({
  apiBase,
  workflowId,
}: {
  apiBase: string;
  workflowId: string;
}): React.ReactElement {
  const [evidenceOpen, setEvidenceOpen] = React.useState(false);
  const [formOpen, setFormOpen] = React.useState(false);
  const [title, setTitle] = React.useState('');
  const [instructions, setInstructions] = React.useState('');
  const [continueState, setContinueState] = React.useState<
    'idle' | 'pending' | 'error'
  >('idle');
  const idempotencyKey = React.useRef<string>('');
  if (!idempotencyKey.current) {
    idempotencyKey.current = newIdempotencyKey();
  }

  const canSubmit =
    instructions.trim().length > 0 && continueState !== 'pending';

  const onSubmit = React.useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (instructions.trim().length === 0 || continueState === 'pending') {
        return;
      }
      setContinueState('pending');
      try {
        const intent: ContinuationIntent = {
          idempotencyKey: idempotencyKey.current,
          instructions: instructions.trim(),
        };
        const trimmedTitle = title.trim();
        if (trimmedTitle) intent.title = trimmedTitle;
        const result = await continueInNewWorkflow(apiBase, workflowId, intent);
        window.location.assign(
          continuationWorkflowHref(result.destinationWorkflowId),
        );
      } catch {
        setContinueState('error');
      }
    },
    [apiBase, workflowId, instructions, title, continueState],
  );

  return (
    <div className="stack td-native-chat-terminal-actions">
      <div className="td-native-chat-actions">
        <button
          type="button"
          className="button secondary"
          aria-expanded={evidenceOpen}
          onClick={() => setEvidenceOpen((open) => !open)}
          data-testid="workflow-native-chat-evidence-toggle"
        >
          View captured evidence
        </button>
        <button
          type="button"
          className="button secondary"
          aria-expanded={formOpen}
          onClick={() => setFormOpen((open) => !open)}
          data-testid="workflow-native-chat-continue"
        >
          Continue in a new workflow
        </button>
      </div>
      {formOpen ? (
        <form
          className="stack td-native-chat-continue-form"
          onSubmit={onSubmit}
          data-testid="workflow-native-chat-continue-form"
        >
          <label className="field">
            <span className="small">Title (optional)</span>
            <input
              type="text"
              value={title}
              maxLength={500}
              onChange={(event) => setTitle(event.target.value)}
              data-testid="workflow-native-chat-continue-title"
            />
          </label>
          <label className="field">
            <span className="small">New instructions</span>
            <textarea
              value={instructions}
              required
              rows={3}
              onChange={(event) => setInstructions(event.target.value)}
              placeholder="Describe what the continuation workflow should do."
              data-testid="workflow-native-chat-continue-instructions"
            />
          </label>
          <button
            type="submit"
            className="button"
            disabled={!canSubmit}
            data-testid="workflow-native-chat-continue-submit"
          >
            {continueState === 'pending' ? 'Continuing…' : 'Start continuation'}
          </button>
        </form>
      ) : null}
      {continueState === 'error' ? (
        <p
          className="small td-native-chat-continue-error"
          role="alert"
          data-testid="workflow-native-chat-continue-error"
        >
          Could not start a linked workflow. Please try again.
        </p>
      ) : null}
      {evidenceOpen ? (
        <CapturedEvidencePanel apiBase={apiBase} workflowId={workflowId} />
      ) : null}
    </div>
  );
}
