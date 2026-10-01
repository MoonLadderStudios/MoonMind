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
};

beforeEach(() => {
  handlers = {
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
      if (url.endsWith('/retry-publication')) return handlers.publish(init);
      if (url.endsWith('/continue')) return handlers.continue(init);
      if (url.endsWith('/captured-evidence')) {
        return jsonResponse({
          workflowId: 'wf-4020',
          available: true,
          items: [{ label: 'Report', kind: 'output_artifact', artifactRef: 'art-report' }],
        });
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
});
