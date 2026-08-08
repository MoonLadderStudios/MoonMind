/**
 * Tests for the native Omnigent Workflow Chat surface
 * (MoonLadderStudios/MoonMind#3638).
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, waitFor } from '@testing-library/react';

import { renderWithClient } from '../utils/test-utils';
import {
  WorkflowChatNative,
  capturedEvidenceHref,
  fullPageChatUrl,
  type CapturedEvidence,
  type ContinueInNewWorkflowResult,
  type WorkflowChatBinding,
} from './WorkflowChatNative';

const API_BASE = '/api';
const WORKFLOW_ID = 'mm:w1';
const CHAT_URL = '/omnigent-ui/workflow-chat/chatb_opaque123?embedded=1';

function bindingResponse(overrides: Partial<WorkflowChatBinding> = {}): WorkflowChatBinding {
  return {
    chatBindingId: 'chatb_opaque123',
    workflowId: WORKFLOW_ID,
    chatUrl: CHAT_URL,
    apiBase: '/api/workflow-chat-bindings/chatb_opaque123/omnigent',
    state: 'available',
    readOnly: false,
    capabilities: {},
    ...overrides,
  } as WorkflowChatBinding;
}

function mockFetch(body: WorkflowChatBinding | null, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      ({
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
      }) as unknown as Response,
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('fullPageChatUrl', () => {
  it('drops only the embedded flag from the scoped chat url', () => {
    expect(fullPageChatUrl(CHAT_URL)).toBe(
      '/omnigent-ui/workflow-chat/chatb_opaque123',
    );
  });

  it('keeps other query parameters and never points at the upstream server', () => {
    expect(
      fullPageChatUrl('/omnigent-ui/workflow-chat/x?embedded=1&foo=bar'),
    ).toBe('/omnigent-ui/workflow-chat/x?foo=bar');
  });
});

describe('WorkflowChatNative', () => {
  it('embeds the native app in an iframe using the scoped chatUrl', async () => {
    mockFetch(bindingResponse());
    const { getByTestId, queryByText } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    const frame = await waitFor(() => getByTestId('workflow-native-chat-frame'));
    expect(frame.getAttribute('src')).toBe(CHAT_URL);
    // The legacy projection is not rendered once native chat is available (no
    // second composer competing with the native UI).
    expect(queryByText('legacy projection')).toBeNull();
  });

  it('offers an Open in Omnigent full-page link on the same scoped binding', async () => {
    mockFetch(bindingResponse());
    const { getByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active />,
    );

    const link = await waitFor(() => getByTestId('workflow-native-chat-open'));
    expect(link.getAttribute('href')).toBe(
      '/omnigent-ui/workflow-chat/chatb_opaque123',
    );
    expect(link.getAttribute('href')).not.toContain('embedded');
  });

  it('falls back to the legacy projection when no native binding exists', async () => {
    mockFetch(null, 404);
    const { findByText, queryByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    expect(await findByText('legacy projection')).toBeTruthy();
    expect(queryByTestId('workflow-native-chat-frame')).toBeNull();
  });

  it('shows the unavailable reason and still renders the fallback', async () => {
    mockFetch(
      bindingResponse({ state: 'unavailable', unavailableReason: 'native_ui_upstream_unavailable', chatUrl: '' }),
    );
    const { findByTestId, findByText } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    expect(await findByTestId('workflow-native-chat-unavailable')).toBeTruthy();
    expect(await findByText('legacy projection')).toBeTruthy();
  });

  it('does not fetch while the chat tab is inactive', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active={false}>
        <div>legacy projection</div>
      </WorkflowChatNative>,
    );

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does not show terminal actions for a live (writeable) session', async () => {
    mockFetch(bindingResponse({ readOnly: false }));
    const { getByTestId, queryByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active />,
    );
    await waitFor(() => getByTestId('workflow-native-chat-frame'));
    expect(queryByTestId('workflow-native-chat-continue')).toBeNull();
    expect(queryByTestId('workflow-native-chat-evidence-toggle')).toBeNull();
  });
});

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
  binding: WorkflowChatBinding;
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
    return { ok: true, status: 200, json: async () => handlers.binding } as unknown as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('capturedEvidenceHref', () => {
  it('builds a workflow-scoped captured-evidence download href from a plain ref', () => {
    expect(capturedEvidenceHref('/api', WORKFLOW_ID, 'art_final')).toBe(
      '/api/executions/mm%3Aw1/captured-evidence/download?ref=art_final',
    );
  });

  it('carries a gateway ref verbatim so the server dispatches it (no scheme stripping)', () => {
    // Gateway refs (artifact://omnigent/<correlation>/<name>) used to 404 against
    // the Temporal-artifact route; the server-side endpoint now resolves them.
    expect(
      capturedEvidenceHref('/api', WORKFLOW_ID, 'artifact://omnigent/c1/final.json'),
    ).toBe(
      '/api/executions/mm%3Aw1/captured-evidence/download' +
        '?ref=artifact%3A%2F%2Fomnigent%2Fc1%2Ffinal.json',
    );
  });

  it('passes a full same-origin URL through and never fabricates a link from empty', () => {
    expect(capturedEvidenceHref('/api', WORKFLOW_ID, 'https://mm/x')).toBe('https://mm/x');
    expect(capturedEvidenceHref('/api', WORKFLOW_ID, '   ')).toBeNull();
  });
});

describe('WorkflowChatNative terminal actions (#3641)', () => {
  it('shows terminal actions for an ended session (not merely read-only)', async () => {
    mockFetchByUrl({ binding: bindingResponse({ state: 'ended', readOnly: true }) });
    const { getByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active />,
    );
    await waitFor(() => getByTestId('workflow-native-chat-frame'));
    expect(getByTestId('workflow-native-chat-evidence-toggle')).toBeTruthy();
    expect(getByTestId('workflow-native-chat-continue')).toBeTruthy();
  });

  it('does not show terminal actions for a read-only live session (no sendMessage)', async () => {
    // Read-only but still available: gating on read-only alone would wrongly
    // offer "Continue in a new workflow" for a live session.
    mockFetchByUrl({ binding: bindingResponse({ state: 'available', readOnly: true }) });
    const { getByTestId, queryByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active />,
    );
    await waitFor(() => getByTestId('workflow-native-chat-frame'));
    expect(queryByTestId('workflow-native-chat-continue')).toBeNull();
    expect(queryByTestId('workflow-native-chat-evidence-toggle')).toBeNull();
  });

  it('renders terminal actions in the fallback when the native iframe is unavailable', async () => {
    // A terminal workflow whose native UI is unavailable must still expose the
    // captured-evidence and continuation controls.
    mockFetchByUrl({
      binding: bindingResponse({
        state: 'unavailable',
        unavailableReason: 'native_ui_upstream_unavailable',
        chatUrl: '',
      }),
    });
    const { findByTestId, queryByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active isTerminal />,
    );
    expect(await findByTestId('workflow-native-chat-terminal')).toBeTruthy();
    expect(await findByTestId('workflow-native-chat-continue')).toBeTruthy();
    // No live iframe in this state.
    expect(queryByTestId('workflow-native-chat-frame')).toBeNull();
  });

  it('opens captured evidence as workflow-scoped download links', async () => {
    mockFetchByUrl({
      binding: bindingResponse({ state: 'ended', readOnly: true }),
      evidence: EVIDENCE,
    });
    const { getByTestId, findAllByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active />,
    );
    await waitFor(() => getByTestId('workflow-native-chat-evidence-toggle'));

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

  it('requires authored intent, then continues into the linked workflow', async () => {
    const fetchMock = mockFetchByUrl({
      binding: bindingResponse({ state: 'ended', readOnly: true }),
      continue: CONTINUE_RESULT,
    });
    const assign = vi.fn();
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, assign },
    });

    const { getByTestId } = renderWithClient(
      <WorkflowChatNative apiBase={API_BASE} workflowId={WORKFLOW_ID} active />,
    );
    await waitFor(() => getByTestId('workflow-native-chat-continue'));

    // Clicking "Continue" opens an authoring form rather than launching
    // immediately, so the continuation cannot become an accidental rerun.
    fireEvent.click(getByTestId('workflow-native-chat-continue'));
    const submit = getByTestId(
      'workflow-native-chat-continue-submit',
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(
      fetchMock.mock.calls.find((call) => String(call[0]).includes('/continue')),
    ).toBeFalsy();

    fireEvent.change(getByTestId('workflow-native-chat-continue-instructions'), {
      target: { value: 'do the follow-up work' },
    });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);

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
    // The continuation is a POST Workflow action carrying the authored intent,
    // not a native composer message.
    expect(continueCall?.[1]?.method).toBe('POST');
    expect(JSON.parse(String(continueCall?.[1]?.body))).toMatchObject({
      instructions: 'do the follow-up work',
    });
  });
});
