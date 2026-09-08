/**
 * Tests for the diagnostics fallback shell
 * (MoonLadderStudios/MoonMind#3638 compatibility surface, #3641 terminal
 * actions, simplified MoonLadderStudios/MoonMind#3956).
 *
 * The single interactive chat application is mounted by
 * `WorkflowNativeChatRoute` (Chat tab); its live behavior — same-origin URL
 * guards, readiness/timeout, retry, unavailable states — is covered by the
 * `features/workflow-native-chat` suites. This suite pins the fallback shell:
 * no fetch, no iframe, no second composer — only the read-only diagnostic
 * projection plus terminal workflow actions.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, waitFor } from '@testing-library/react';

import { renderWithClient } from '../utils/test-utils';
import { WorkflowChatNative } from './WorkflowChatNative';
import {
  capturedEvidenceHref,
  type CapturedEvidence,
  type ContinueInNewWorkflowResult,
} from '../features/workflow-native-chat/WorkflowTerminalChatActions';

const API_BASE = '/api';
const WORKFLOW_ID = 'mm:w1';

const EVIDENCE: CapturedEvidence = {
  workflowId: WORKFLOW_ID,
  runId: 'run-1',
  available: true,
  items: [
    { label: 'Final snapshot', kind: 'final_snapshot', artifactRef: 'art_final' },
    { label: 'Capture manifest', kind: 'capture_manifest', artifactRef: 'artifact://art_manifest' },
  ],
};

const CONTINUE_RESULT: ContinueInNewWorkflowResult = {
  sourceWorkflowId: WORKFLOW_ID,
  sourceRunId: 'run-1',
  destinationWorkflowId: 'mm:continuation',
  relationshipType: 'linked_continuation',
  created: true,
};

function mockFetchByUrl(handlers: {
  evidence?: CapturedEvidence;
  continue?: ContinueInNewWorkflowResult;
}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/captured-evidence')) {
      return { ok: true, status: 200, json: async () => handlers.evidence } as unknown as Response;
    }
    if (url.includes('/continue')) {
      return { ok: true, status: 201, json: async () => handlers.continue } as unknown as Response;
    }
    throw new Error(`unexpected fetch in fallback shell: ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('WorkflowChatNative fallback shell (#3956)', () => {
  it('renders the read-only diagnostic projection without fetching or mounting a frame', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { getByText, queryByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID}>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    expect(getByText('legacy projection')).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    // No live native surface here: the Chat tab owns the single iframe.
    expect(queryByTestId('workflow-native-chat-frame')).toBeNull();
    expect(queryByTestId('workflow-native-chat-open')).toBeNull();
    expect(queryByTestId('workflow-native-chat-unavailable')).toBeNull();
  });

  it('does not show terminal actions for a non-terminal workflow', () => {
    const { queryByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} terminal={false} />,
    );
    expect(queryByTestId('workflow-native-chat-continue')).toBeNull();
    expect(queryByTestId('workflow-native-chat-evidence-toggle')).toBeNull();
  });

  it('shows View captured evidence and Continue for a terminal workflow', async () => {
    mockFetchByUrl({});
    const { getByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} terminal />,
    );
    expect(getByTestId('workflow-native-chat-evidence-toggle')).toBeTruthy();
    expect(getByTestId('workflow-native-chat-continue')).toBeTruthy();
  });

  it('keeps terminal actions alongside the diagnostic projection', async () => {
    mockFetchByUrl({});
    const { getByTestId, getByText } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} terminal>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );
    expect(getByText('legacy projection')).toBeTruthy();
    expect(getByTestId('workflow-native-chat-evidence-toggle')).toBeTruthy();
    expect(getByTestId('workflow-native-chat-continue')).toBeTruthy();
  });
});

describe('capturedEvidenceHref', () => {
  it('routes a plain ref through the workflow-scoped evidence download endpoint', () => {
    expect(capturedEvidenceHref('/api', WORKFLOW_ID, 'art_final')).toBe(
      '/api/executions/mm%3Aw1/captured-evidence/download?ref=art_final',
    );
  });

  it('carries an Omnigent gateway ref (scheme + slashes) intact as a query param', () => {
    expect(
      capturedEvidenceHref('/api', WORKFLOW_ID, 'artifact://omnigent/corr-1/final.json'),
    ).toBe(
      '/api/executions/mm%3Aw1/captured-evidence/download?ref=artifact%3A%2F%2Fomnigent%2Fcorr-1%2Ffinal.json',
    );
  });

  it('passes a full same-origin URL through and never fabricates a link from empty', () => {
    expect(capturedEvidenceHref('/api', WORKFLOW_ID, 'https://mm/x')).toBe('https://mm/x');
    expect(capturedEvidenceHref('/api', WORKFLOW_ID, '   ')).toBeNull();
    expect(capturedEvidenceHref('/api', '', 'art_final')).toBeNull();
  });
});

describe('WorkflowChatNative terminal actions (#3641)', () => {
  it('opens captured evidence as authorized MoonMind download links', async () => {
    mockFetchByUrl({ evidence: EVIDENCE });
    const { getByTestId, findAllByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} terminal />,
    );

    fireEvent.click(getByTestId('workflow-native-chat-evidence-toggle'));
    const links = await findAllByTestId('workflow-native-chat-evidence-link');
    expect(links).toHaveLength(2);
    expect(links[0]!.getAttribute('href')).toBe(
      '/api/executions/mm%3Aw1/captured-evidence/download?ref=art_final',
    );
    expect(links[1]!.getAttribute('href')).toBe(
      '/api/executions/mm%3Aw1/captured-evidence/download?ref=artifact%3A%2F%2Fart_manifest',
    );
  });

  it('requires authored intent before launching a continuation', async () => {
    const fetchMock = mockFetchByUrl({ continue: CONTINUE_RESULT });
    const { getByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} terminal />,
    );

    fireEvent.click(getByTestId('workflow-native-chat-continue'));
    const submit = getByTestId('workflow-native-chat-continue-submit') as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(
      fetchMock.mock.calls.some((call) => String(call[0]).includes('/continue')),
    ).toBe(false);
  });

  it('continues into the linked workflow with authored intent and navigates to it', async () => {
    const fetchMock = mockFetchByUrl({ continue: CONTINUE_RESULT });
    const assign = vi.fn();
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, assign },
    });

    const { getByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} terminal />,
    );

    fireEvent.click(getByTestId('workflow-native-chat-continue'));
    fireEvent.change(getByTestId('workflow-native-chat-continue-instructions'), {
      target: { value: 'Do the follow-up work' },
    });
    fireEvent.submit(getByTestId('workflow-native-chat-continue-form'));

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(
        '/workflows/mm%3Acontinuation?source=temporal',
      ),
    );
    const continueCall = fetchMock.mock.calls.find((call) =>
      String(call[0]).includes('/continue'),
    );
    expect(continueCall).toBeTruthy();
    expect(String(continueCall?.[0])).toContain(
      `/executions/${encodeURIComponent(WORKFLOW_ID)}/continue`,
    );
    expect(continueCall?.[1]?.method).toBe('POST');
    const body = JSON.parse(String(continueCall?.[1]?.body));
    expect(body.instructions).toBe('Do the follow-up work');
    expect(typeof body.idempotencyKey).toBe('string');
  });
});
