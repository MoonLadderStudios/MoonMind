import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import { fireEvent, renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import { SavedResultsSection } from './saved-results-section';

// Component flows for MoonLadderStudios/MoonMind#4020. Every action goes
// through the real dispatch functions against the server request/response
// contracts (`/retry-publication` with the saved-work body, `/continue` with an
// idempotency key), with only `fetch` replaced.

const SAVED_WORK = 'application/vnd.moonmind.saved-work-manifest+json;version=1';

function savedArtifacts(overrides: { manifestRawAccess?: boolean; deltaRawAccess?: boolean } = {}) {
  const complete = {
    status: 'complete',
    sha256: 'abc123',
    sizeBytes: 64,
    defaultReadRef: null,
    downloadUrl: null,
  };
  return [
    {
      ...complete,
      artifactId: 'art-manifest',
      contentType: SAVED_WORK,
      rawAccessAllowed: overrides.manifestRawAccess ?? true,
      retention_class: 'standard',
      metadata: {
        artifact_kind: 'saved_work_manifest',
        saved_work_summary: {
          capture_id: 'implement:capture',
          required_formats: ['full_snapshot'],
          outputs: [
            { format: 'full_snapshot', status: 'self_contained', artifact_id: 'art-archive' },
            {
              format: 'exact_baseline_delta',
              status: 'requires_dependencies',
              artifact_id: 'art-delta',
            },
          ],
          exclusion_count: 2,
          exclusion_reasons: [{ reason: 'sensitive-path-policy', count: 2 }],
          limitations: [],
          retention_ref: 'artifact-ownership',
        },
      },
      links: [{ linkType: 'output.checkpoint' }],
    },
    {
      ...complete,
      artifactId: 'art-archive',
      contentType: 'application/vnd.moonmind.worktree-archive',
      rawAccessAllowed: true,
      metadata: { artifact_kind: 'checkpoint_archive' },
      links: [{ linkType: 'output.checkpoint' }],
    },
    {
      ...complete,
      artifactId: 'art-delta',
      contentType: 'application/vnd.moonmind.saved-work-delta+json;version=1',
      rawAccessAllowed: overrides.deltaRawAccess ?? true,
      metadata: { artifact_kind: 'checkpoint_delta' },
      links: [{ linkType: 'output.checkpoint' }],
    },
    {
      ...complete,
      artifactId: 'art-report',
      contentType: 'text/markdown',
      rawAccessAllowed: true,
      metadata: { title: 'Final report' },
      links: [{ linkType: 'report.summary' }],
    },
  ];
}

const failedExecution = {
  workflowId: 'wf-4020',
  runId: 'run-1',
  state: 'failed',
  repository: 'Owner/Repo',
  startingBranch: 'main',
  publishMode: 'pr',
  // The execution detail's action projection, as the server serializes it.
  actions: {
    canRetryPublication: false,
    canPublishSavedWork: true,
    actionEvidence: { publishSavedWork: { allowedModes: ['pr', 'draft_pr', 'branch'] } },
    disabledReasons: { canRetryPublication: 'publication_retry_not_eligible' },
  },
  finishSummary: {
    controlStop: {
      auxiliaryOutcomes: {
        gitPublication: { status: 'failed' },
        hostCleanup: { status: 'pending' },
        providerProfileRelease: { status: 'pending' },
        janitorRequired: false,
      },
    },
  },
};

const publicationDisabledExecution = {
  ...failedExecution,
  actions: {
    canRetryPublication: false,
    canPublishSavedWork: false,
    actionEvidence: {},
    disabledReasons: {
      canRetryPublication: 'publication_retry_not_eligible',
      canPublishSavedWork: 'publication_recovery_disabled',
    },
  },
};

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

const publication = {
  sourceWorkflowId: 'wf-4020',
  sourceRunId: 'run-1',
  workflowId: 'mm:publication:abc',
  runId: 'publication-run',
  publicationIdempotencyKey: 'saved-publication:abc',
  rolloutGeneration: 'g1',
};

let fetchSpy: MockInstance;
let handlers: {
  publish: (init: RequestInit | undefined) => Promise<Response>;
  continue: (init: RequestInit | undefined) => Promise<Response>;
  publicationRun: () => Promise<Response>;
  capturedEvidence: () => Promise<Response>;
};

const PUBLICATION_RUN_URL = '/api/executions/mm%3Apublication%3Aabc?source=temporal';

beforeEach(() => {
  window.sessionStorage.clear();
  handlers = {
    capturedEvidence: async () =>
      jsonResponse({
        workflowId: 'wf-4020',
        available: true,
        items: [{ label: 'Report', kind: 'output_artifact', artifactRef: 'art-report' }],
      }),
    publicationRun: async () =>
      jsonResponse({
        workflowId: 'mm:publication:abc',
        workflowType: 'MoonMind.PublicationRecoveryV1',
        state: 'executing',
        temporalStatus: 'running',
      }),
    publish: async () => jsonResponse(publication, 201),
    continue: async () =>
      jsonResponse(
        {
          sourceWorkflowId: 'wf-4020',
          sourceRunId: 'run-1',
          destinationWorkflowId: 'mm:continuation',
          relationshipType: 'linked_continuation',
          created: true,
        },
        201,
      ),
  };
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === PUBLICATION_RUN_URL) return handlers.publicationRun();
      if (url.endsWith('/retry-publication')) return handlers.publish(init);
      if (url.endsWith('/continue')) return handlers.continue(init);
      if (url.endsWith('/captured-evidence')) {
        return handlers.capturedEvidence();
      }
      return jsonResponse({});
    },
  );
});

afterEach(() => {
  fetchSpy.mockRestore();
});

function calls(suffix: string) {
  return fetchSpy.mock.calls.filter(([input]) => String(input).endsWith(suffix));
}

function bodyOf(call: unknown[] | undefined) {
  return JSON.parse(String((call?.[1] as RequestInit | undefined)?.body));
}

function renderSection(
  props: Partial<Parameters<typeof SavedResultsSection>[0]> = {},
) {
  const onRefresh = vi.fn();
  const element = (overrides: Partial<Parameters<typeof SavedResultsSection>[0]> = {}) => (
    <SavedResultsSection
      workflowId="wf-4020"
      runId="run-1"
      apiBase="/api"
      execution={failedExecution}
      artifacts={savedArtifacts()}
      isLoading={false}
      error={null}
      stale={false}
      onRefresh={onRefresh}
      actionsEnabled
      {...props}
      {...overrides}
    />
  );
  const view = renderWithClient(element());
  return { ...view, onRefresh, rerenderWith: (overrides = {}) => view.rerender(element(overrides)) };
}

async function openPublishForm() {
  fireEvent.click(screen.getByRole('button', { name: 'Publish saved work' }));
  return screen.findByRole('form', { name: 'Publish saved work' });
}

describe('Saved Results section', () => {
  it('keeps compute, save, publication, and cleanup outcomes separate', () => {
    renderSection();
    const text = (label: string) =>
      screen.getByText(new RegExp(`^${label}:`), { selector: 'strong' }).closest('.card')
        ?.textContent;
    expect(text('Compute')).toMatch(/Compute:\s*failed/i);
    expect(text('Save')).toMatch(/Save:\s*committed/i);
    expect(text('Publication')).toMatch(/Publication:\s*failed/i);
    expect(text('Cleanup')).toMatch(/Cleanup:\s*pending/i);
    // The saved unit lists its parts once, not as separate saved results.
    const parts = screen.getByRole('list', { name: 'Saved work parts' });
    expect(within(parts).getByText('art-archive')).toBeTruthy();
    expect(screen.getAllByText('art-archive')).toHaveLength(1);
    expect(screen.getByText('2 excluded')).toBeTruthy();
  });

  it('publishes the selected saved work to the edited destination once', async () => {
    renderSection();
    const form = await openPublishForm();
    const head = within(form).getByLabelText('Head branch') as HTMLInputElement;
    expect(head.value).toBe('saved-work/wf-4020');
    fireEvent.change(head, { target: { value: 'saved-work/edited' } });
    fireEvent.change(within(form).getByLabelText('Publish as'), {
      target: { value: 'draft_pr' },
    });

    fireEvent.submit(form);
    fireEvent.submit(form);

    expect(await screen.findByText(/Publication started for Owner\/Repo/)).toBeTruthy();
    expect(calls('/retry-publication')).toHaveLength(1);
    const [call] = calls('/retry-publication');
    expect(String(call?.[0])).toBe('/api/executions/wf-4020/retry-publication');
    expect(bodyOf(call)).toEqual({
      savedWorkRef: 'art-manifest',
      admissionGeneration: expect.any(String),
      sourceRunId: 'run-1',
      destination: {
        repository: 'Owner/Repo',
        objective: 'draft_pr',
        baseBranch: 'main',
        headBranch: 'saved-work/edited',
        strategy: 'baseline_delta',
      },
    });
    const started = screen.getByText(/Publication started/);
    expect(within(started).getByRole('link', { name: 'mm:publication:abc' })).toBeTruthy();
    expect(within(started).getByText('saved-publication:abc')).toBeTruthy();

    // A changed destination is a new decision; the accepted operation stays
    // visible because leaving or editing the form never cancels it.
    fireEvent.change(head, { target: { value: 'saved-work/other' } });
    expect(screen.getByText(/The destination changed since the last request/)).toBeTruthy();
    expect(screen.getByText(/Publication started/)).toBeTruthy();
    fireEvent.click(within(form).getByRole('button', { name: 'Close' }));
    expect(screen.getByText(/Publication started/)).toBeTruthy();
  });

  it('publishes artifacts from the selected run when the execution advances', async () => {
    renderSection({ runId: 'run-selected', execution: { ...failedExecution, runId: 'run-current' } });
    fireEvent.submit(await openPublishForm());
    expect(await screen.findByText(/Publication started/)).toBeTruthy();
    expect(bodyOf(calls('/retry-publication')[0]).sourceRunId).toBe('run-selected');
    expect(screen.queryByText(/The destination changed since the last request/)).toBeNull();
  });

  it.each([408, 500, 502, 503, 504])(
    'reconciles an uncertain publication response (%s) using the same request',
    async (status) => {
      let attempt = 0;
      handlers.publish = async () => {
        attempt += 1;
        return attempt === 1
          ? jsonResponse({ detail: 'The start acknowledgment was lost.' }, status)
          : jsonResponse(publication, 201);
      };
      renderSection();
      const form = await openPublishForm();
      fireEvent.submit(form);
      expect(await screen.findByText(/may\s+have been accepted/)).toBeTruthy();
      expect(screen.queryByText(/Publication started/)).toBeNull();
      fireEvent.submit(form);
      expect(await screen.findByText(/Publication started/)).toBeTruthy();
      const [first, second] = calls('/retry-publication');
      expect(bodyOf(second)).toEqual(bodyOf(first));
    },
  );

  it('reuses the same publication request after a lost acknowledgment', async () => {
    let attempt = 0;
    handlers.publish = async () => {
      attempt += 1;
      if (attempt === 1) throw new TypeError('Failed to fetch');
      return jsonResponse(publication, 201);
    };
    renderSection();
    const form = await openPublishForm();
    fireEvent.submit(form);
    expect(await screen.findByText(/may\s+have been accepted/)).toBeTruthy();
    expect(screen.queryByText(/Publication started/)).toBeNull();

    fireEvent.submit(form);
    expect(await screen.findByText(/Publication started/)).toBeTruthy();
    const [first, second] = calls('/retry-publication');
    expect(bodyOf(second)).toEqual(bodyOf(first));
  });

  it('retains the admission generation across reload after an ambiguous acknowledgment', async () => {
    handlers.publish = async () => { throw new TypeError('Failed to fetch'); };
    const firstView = renderSection();
    fireEvent.submit(await openPublishForm());
    expect(await screen.findByText(/may\s+have been accepted/)).toBeTruthy();
    const original = bodyOf(calls('/retry-publication')[0]);
    expect(original.admissionGeneration).toEqual(expect.any(String));
    firstView.unmount();

    handlers.publish = async () => jsonResponse(publication, 201);
    renderSection();
    fireEvent.submit(await openPublishForm());
    expect(await screen.findByText(/Publication started/)).toBeTruthy();
    expect(bodyOf(calls('/retry-publication')[1])).toEqual(original);
  });

  it('explicitly re-admits unchanged output after the prior publication is fenced', async () => {
    let reads = 0;
    handlers.publicationRun = async () => {
      reads += 1;
      return jsonResponse({ state: 'failed', temporalStatus: 'failed' });
    };
    renderSection();
    const form = await openPublishForm();
    fireEvent.submit(form);
    expect(await screen.findByText(/Publication failed/)).toBeTruthy();
    expect(reads).toBeGreaterThan(0);
    const original = bodyOf(calls('/retry-publication')[0]);
    expect(original.admissionGeneration).toEqual(expect.any(String));
    fireEvent.submit(form);
    await waitFor(() => expect(calls('/retry-publication')).toHaveLength(2));
    const next = bodyOf(calls('/retry-publication')[1]);
    expect(next.admissionGeneration).not.toBe(original.admissionGeneration);
    expect({ ...next, admissionGeneration: original.admissionGeneration }).toEqual(original);
  });

  it('keeps an uncertain admission after a later API denial', async () => {
    let attempt = 0;
    handlers.publish = async () => {
      attempt += 1;
      if (attempt === 1) throw new TypeError('Failed to fetch');
      if (attempt === 2) return jsonResponse({ detail: 'Session expired' }, 401);
      return jsonResponse(publication, 201);
    };
    renderSection();
    const form = await openPublishForm();
    fireEvent.submit(form);
    expect(await screen.findByText(/may\s+have been accepted/)).toBeTruthy();
    fireEvent.submit(form);
    expect(await screen.findByText('Session expired')).toBeTruthy();
    fireEvent.submit(form);
    expect(await screen.findByText(/Publication started/)).toBeTruthy();
    const [first, denied, recovered] = calls('/retry-publication');
    expect(bodyOf(denied)).toEqual(bodyOf(first));
    expect(bodyOf(recovered)).toEqual(bodyOf(first));
  });

  it('does not use an older terminal operation to rotate a newer ambiguous generation', async () => {
    let attempts = 0;
    handlers.publicationRun = async () => jsonResponse({ state: 'failed', temporalStatus: 'failed' });
    handlers.publish = async () => {
      attempts += 1;
      if (attempts > 1) throw new TypeError('Lost new admission acknowledgment');
      return jsonResponse(publication, 201);
    };
    const firstView = renderSection();
    const form = await openPublishForm();
    fireEvent.submit(form);
    expect(await screen.findByText(/Publication failed/)).toBeTruthy();
    fireEvent.submit(form);
    expect(await screen.findByText(/may\s+have been accepted/)).toBeTruthy();
    const [first, second] = calls('/retry-publication');
    expect(bodyOf(second).admissionGeneration).not.toBe(bodyOf(first).admissionGeneration);
    fireEvent.submit(form);
    await waitFor(() => expect(calls('/retry-publication')).toHaveLength(3));
    expect(bodyOf(calls('/retry-publication')[2])).toEqual(bodyOf(second));
    firstView.unmount();
    renderSection();
    fireEvent.submit(await openPublishForm());
    await waitFor(() => expect(calls('/retry-publication')).toHaveLength(4));
    expect(bodyOf(calls('/retry-publication')[3])).toEqual(bodyOf(second));
  });

  it('generates a fresh HTTP-compatible admission when randomUUID is unavailable', async () => {
    const originalUUID = Object.getOwnPropertyDescriptor(crypto, 'randomUUID');
    Object.defineProperty(crypto, 'randomUUID', { configurable: true, value: undefined });
    try {
      renderSection();
      fireEvent.submit(await openPublishForm());
      expect(await screen.findByText(/Publication started/)).toBeTruthy();
      expect(bodyOf(calls('/retry-publication')[0]).admissionGeneration).toMatch(
        /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/,
      );
    } finally {
      if (originalUUID) Object.defineProperty(crypto, 'randomUUID', originalUUID);
      else Reflect.deleteProperty(crypto, 'randomUUID');
    }
  });

  it('does not submit when a request identity cannot survive browser reload', async () => {
    const storage = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage disabled', 'SecurityError');
    });
    try {
      renderSection();
      fireEvent.submit(await openPublishForm());
      expect(await screen.findByText(/browser could not retain its request/)).toBeTruthy();
      expect(calls('/retry-publication')).toHaveLength(0);
    } finally { storage.mockRestore(); }
  });

  it('shows a server denial without claiming publication', async () => {
    handlers.publish = async () =>
      jsonResponse(
        {
          detail: {
            code: 'saved_work_source_mismatch',
            message: 'Saved work does not belong to this execution.',
          },
        },
        409,
      );
    renderSection();
    fireEvent.submit(await openPublishForm());
    expect(await screen.findByText('Saved work does not belong to this execution.')).toBeTruthy();
    expect(screen.queryByText(/Publication started/)).toBeNull();
  });

  it('ignores a late publication response after the selection changes', async () => {
    const pending = deferred<Response>();
    handlers.publish = () => pending.promise;
    const view = renderSection();
    fireEvent.submit(await openPublishForm());
    await waitFor(() => expect(calls('/retry-publication')).toHaveLength(1));

    view.rerenderWith({ runId: 'run-2', execution: { ...failedExecution, runId: 'run-2' } });
    pending.resolve(jsonResponse(publication, 201));
    await new Promise((settle) => setTimeout(settle, 0));

    expect(screen.queryByText(/Publication started/)).toBeNull();
    expect(screen.queryByRole('form', { name: 'Publish saved work' })).toBeNull();
    expect(screen.getByText(/Selected run run-2/)).toBeTruthy();
  });

  it('continues through fresh admission with a stable intent key', async () => {
    renderSection();
    const toggle = screen.getByRole('button', { name: 'Continue working' }) as HTMLButtonElement;
    await waitFor(() => expect(toggle.disabled).toBe(false));
    fireEvent.click(toggle);
    const form = screen.getByRole('form', { name: 'Continue working' });
    fireEvent.change(within(form).getByLabelText('New instructions'), {
      target: { value: 'Finish the report.' },
    });
    fireEvent.submit(form);
    expect(await screen.findByText(/Continuation admitted/)).toBeTruthy();

    handlers.continue = async () =>
      jsonResponse(
        {
          sourceWorkflowId: 'wf-4020',
          sourceRunId: 'run-1',
          destinationWorkflowId: 'mm:continuation',
          relationshipType: 'linked_continuation',
          created: false,
        },
        200,
      );
    fireEvent.submit(form);
    expect(await screen.findByText(/existing continuation reused/)).toBeTruthy();

    const [first, second] = calls('/continue').map(bodyOf);
    expect(first).toEqual({
      idempotencyKey: first.idempotencyKey,
      instructions: 'Finish the report.',
      selectedSourceArtifactRefs: ['art-report'],
    });
    expect(first.idempotencyKey).toMatch(/^saved-result:continue:wf-4020:run-1:/);
    expect(second.idempotencyKey).toBe(first.idempotencyKey);
    expect(
      screen.getByRole('link', { name: 'mm:continuation' }).getAttribute('href'),
    ).toBe('/workflows/mm%3Acontinuation?source=temporal');
  });

  it('refreshes protected content when the server denies admission', async () => {
    handlers.continue = async () =>
      jsonResponse(
        {
          detail: {
            code: 'continuation_evidence_unauthorized',
            message: 'One or more selected source evidence refs are not authorized.',
          },
        },
        403,
      );
    const view = renderSection();
    const toggle = screen.getByRole('button', { name: 'Continue working' }) as HTMLButtonElement;
    await waitFor(() => expect(toggle.disabled).toBe(false));
    fireEvent.click(toggle);
    const form = screen.getByRole('form', { name: 'Continue working' });
    fireEvent.change(within(form).getByLabelText('New instructions'), {
      target: { value: 'Finish the report.' },
    });
    fireEvent.submit(form);
    expect(
      await screen.findByText('One or more selected source evidence refs are not authorized.'),
    ).toBeTruthy();
    expect(screen.queryByText(/Continuation admitted/)).toBeNull();
    expect(view.onRefresh).toHaveBeenCalled();
  });

  it('refetches captured evidence after denial before retrying continuation', async () => {
    let attempt = 0;
    handlers.continue = async () => {
      attempt += 1;
      if (attempt === 1) {
        handlers.capturedEvidence = async () =>
          jsonResponse({ workflowId: 'wf-4020', available: true, items: [] });
        return jsonResponse({ detail: 'The selected refs are no longer authorized.' }, 403);
      }
      return jsonResponse({ destinationWorkflowId: 'mm:continuation', created: true }, 201);
    };
    renderSection();
    const toggle = screen.getByRole('button', { name: 'Continue working' }) as HTMLButtonElement;
    await waitFor(() => expect(toggle.disabled).toBe(false));
    fireEvent.click(toggle);
    const form = screen.getByRole('form', { name: 'Continue working' });
    fireEvent.change(within(form).getByLabelText('New instructions'), {
      target: { value: 'Finish the report.' },
    });
    fireEvent.submit(form);
    expect(await screen.findByText('The selected refs are no longer authorized.')).toBeTruthy();
    await waitFor(() => expect(calls('/captured-evidence')).toHaveLength(2));
    await waitFor(() =>
      expect(within(form).getByRole('button', { name: 'Start continuation' }).hasAttribute('disabled')).toBe(false),
    );
    fireEvent.submit(form);
    expect(await screen.findByText(/Continuation admitted/)).toBeTruthy();
    const [first, second] = calls('/continue').map(bodyOf);
    expect(first.selectedSourceArtifactRefs).toEqual(['art-report']);
    expect(second.selectedSourceArtifactRefs).toBeUndefined();
    expect(second.idempotencyKey).not.toEqual(first.idempotencyKey);
  });

  it('honors raw-access denials for parts and for publication', () => {
    renderSection({ artifacts: savedArtifacts({ manifestRawAccess: false, deltaRawAccess: false }) });
    const parts = screen.getByRole('list', { name: 'Saved work parts' });
    const delta = within(parts).getByText('art-delta').closest('li') as HTMLElement;
    expect(within(delta).queryByRole('link', { name: 'Download' })).toBeNull();
    expect(within(delta).getByText('Raw unavailable')).toBeTruthy();
    const publish = screen.getByRole('button', { name: 'Publish saved work' }) as HTMLButtonElement;
    expect(publish.disabled).toBe(true);
    expect(publish.title).toMatch(/raw access/);
  });

  it('keeps actions unavailable when workflow actions are disabled', () => {
    renderSection({ actionsEnabled: false });
    expect(
      (screen.getByRole('button', { name: 'Publish saved work' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole('button', { name: 'Continue working' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('reports a server-disabled publication without blocking download or continue', async () => {
    renderSection({ execution: publicationDisabledExecution });

    // The server's reason is shown, distinct from the save's own state.
    const note = screen.getByRole('note');
    expect(note.textContent).toMatch(/Publish saved work is unavailable/);
    expect(note.textContent).toMatch(/publication rollout policy/);
    const text = (label: string) =>
      screen.getByText(new RegExp(`^${label}:`), { selector: 'strong' }).closest('.card')
        ?.textContent;
    expect(text('Save')).toMatch(/Save:\s*committed/i);
    expect(text('Compute')).toMatch(/Compute:\s*failed/i);
    const publish = screen.getByRole('button', { name: 'Publish saved work' }) as HTMLButtonElement;
    expect(publish.disabled).toBe(true);
    expect(publish.title).toMatch(/publication rollout policy/);
    fireEvent.click(publish);
    expect(screen.queryByRole('form', { name: 'Publish saved work' })).toBeNull();
    expect(calls('/retry-publication')).toHaveLength(0);

    // Ordinary downloads and Continue stay available.
    const parts = screen.getByRole('list', { name: 'Saved work parts' });
    expect(within(parts).getAllByRole('link', { name: 'Download' }).length).toBeGreaterThan(0);
    const toggle = screen.getByRole('button', { name: 'Continue working' }) as HTMLButtonElement;
    await waitFor(() => expect(toggle.disabled).toBe(false));
    fireEvent.click(toggle);
    const form = screen.getByRole('form', { name: 'Continue working' });
    fireEvent.change(within(form).getByLabelText('New instructions'), {
      target: { value: 'Finish the report.' },
    });
    fireEvent.submit(form);
    expect(await screen.findByText(/Continuation admitted/)).toBeTruthy();
    expect(calls('/continue')).toHaveLength(1);
    expect(calls('/retry-publication')).toHaveLength(0);
  });

  it('treats an unreported publication capability as unavailable', () => {
    const { actions: _actions, ...withoutActions } = failedExecution;
    renderSection({ execution: withoutActions });
    expect(
      (screen.getByRole('button', { name: 'Publish saved work' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByRole('note').textContent).toMatch(/has not reported/);
  });

  it('refreshes availability when the server refuses publication by policy', async () => {
    handlers.publish = async () =>
      jsonResponse(
        {
          detail: {
            code: 'publication_retry_not_admitted',
            message: 'Publication is not admitted by current rollout policy.',
            reason: 'publication_recovery_disabled',
          },
        },
        409,
      );
    const view = renderSection();
    fireEvent.submit(await openPublishForm());
    expect(
      await screen.findByText('Publication is not admitted by current rollout policy.'),
    ).toBeTruthy();
    expect(screen.queryByText(/Publication started/)).toBeNull();
    expect(view.onRefresh).toHaveBeenCalled();
  });

  it('offers only the publication modes the server admits', async () => {
    renderSection({
      execution: {
        ...failedExecution,
        actions: {
          ...failedExecution.actions,
          actionEvidence: {
            publishSavedWork: { allowedModes: ['branch'], canaryRepositories: ['Owner/Repo'] },
          },
        },
      },
    });
    const form = await openPublishForm();
    const mode = within(form).getByLabelText('Publish as') as HTMLSelectElement;
    expect(Array.from(mode.options).map((option) => option.value)).toEqual(['branch']);
    expect(mode.value).toBe('branch');
    expect(within(form).getByText(/rollout currently admits only Owner\/Repo/)).toBeTruthy();
  });

  it('follows the returned publication operation to its persisted outcome', async () => {
    handlers.publicationRun = async () =>
      jsonResponse({
        workflowId: 'mm:publication:abc',
        workflowType: 'MoonMind.PublicationRecoveryV1',
        state: 'failed',
        rawState: 'failed',
        temporalStatus: 'failed',
        closeStatus: 'timed_out',
      });
    renderSection();
    const form = await openPublishForm();
    fireEvent.submit(form);
    expect(await screen.findByText(/Publication started/)).toBeTruthy();

    const outcome = await screen.findByRole('status', { name: 'Publication outcome' });
    await waitFor(() => expect(outcome.textContent).toMatch(/^Publication failed\./));
    expect(outcome.textContent).toMatch(/saved work is unchanged/);
    expect(calls(PUBLICATION_RUN_URL).length).toBeGreaterThan(0);
    for (const [, init] of calls(PUBLICATION_RUN_URL)) {
      expect((init as RequestInit | undefined)?.method ?? 'GET').toBe('GET');
    }
    // Following the operation never starts more work: one publication request
    // and nothing else is posted.
    const posts = fetchSpy.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
    );
    expect(posts.map(([input]) => String(input))).toEqual([
      '/api/executions/wf-4020/retry-publication',
    ]);

    // Closing the form does not stop following the accepted operation.
    fireEvent.click(within(form).getByRole('button', { name: 'Close' }));
    expect(screen.getByRole('status', { name: 'Publication outcome' })).toBeTruthy();
  });

  it('shows a completed publication run without claiming more than the run records', async () => {
    handlers.publicationRun = async () =>
      jsonResponse({
        workflowId: 'mm:publication:abc',
        state: 'completed',
        temporalStatus: 'completed',
      });
    renderSection();
    fireEvent.submit(await openPublishForm());
    const outcome = await screen.findByRole('status', { name: 'Publication outcome' });
    await waitFor(() => expect(outcome.textContent).toMatch(/^Publication completed/));
  });

  it('keeps simple destination choices first and reveals advanced ones when asked', async () => {
    renderSection();
    const form = await openPublishForm();
    expect(within(form).getByLabelText('Repository')).toBeTruthy();
    expect(within(form).getByLabelText('Publish as')).toBeTruthy();
    expect(within(form).getByLabelText('Head branch')).toBeTruthy();
    expect(within(form).queryByLabelText('Base branch')).toBeNull();
    expect(within(form).queryByLabelText('Apply saved work as')).toBeNull();
    expect(within(form).getByText('main')).toBeTruthy();

    fireEvent.click(
      within(form).getByRole('button', { name: 'Change base or how saved work is applied' }),
    );
    const advanced = within(form).getByRole('group', { name: 'Advanced destination options' });
    fireEvent.change(within(advanced).getByLabelText('Apply saved work as'), {
      target: { value: 'empty_initialization' },
    });
    const objective = within(form).getByLabelText('Publish as') as HTMLSelectElement;
    expect(objective.value).toBe('branch');
    expect(objective.disabled).toBe(true);
    // An empty destination has no base, so none is sent.
    expect(within(advanced).queryByLabelText('Base branch')).toBeNull();
    fireEvent.submit(form);
    expect(await screen.findByText(/Publication started/)).toBeTruthy();
    expect(bodyOf(calls('/retry-publication')[0])).toEqual({
      savedWorkRef: 'art-manifest',
      admissionGeneration: expect.any(String),
      sourceRunId: 'run-1',
      destination: {
        repository: 'Owner/Repo',
        objective: 'branch',
        headBranch: 'saved-work/wf-4020',
        strategy: 'empty_initialization',
      },
    });
  });

  it('offers empty initialization only when branch publication is admitted', async () => {
    renderSection({
      execution: {
        ...failedExecution,
        actions: {
          ...failedExecution.actions,
          actionEvidence: { publishSavedWork: { allowedModes: ['pr'] } },
        },
      },
    });
    const form = await openPublishForm();
    fireEvent.click(within(form).getByRole('button', { name: 'Change base or how saved work is applied' }));
    const empty = within(form).getByRole('option', { name: 'Initialize an empty destination' }) as HTMLOptionElement;
    expect(empty.disabled).toBe(true);
  });

  it('reveals the base branch when the destination still needs one', async () => {
    const { startingBranch: _base, ...withoutBase } = failedExecution;
    renderSection({ execution: withoutBase });
    const form = await openPublishForm();
    const advanced = within(form).getByRole('group', { name: 'Advanced destination options' });
    expect(within(advanced).getByText(/needs its base branch/)).toBeTruthy();
    fireEvent.change(within(advanced).getByLabelText('Base branch'), {
      target: { value: 'develop' },
    });
    // The choice stays visible after it is filled in.
    expect(
      (within(form).getByLabelText('Base branch') as HTMLInputElement).value,
    ).toBe('develop');
    fireEvent.submit(form);
    expect(await screen.findByText(/Publication started/)).toBeTruthy();
    expect(bodyOf(calls('/retry-publication')[0]).destination.baseBranch).toBe('develop');
  });
});
