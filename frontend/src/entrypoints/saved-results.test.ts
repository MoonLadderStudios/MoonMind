import { describe, expect, it } from 'vitest';
import {
  buildContinueInNewWorkflowBody,
  buildSavedResultDownloadHref,
  buildSavedResultPreviewHref,
  canDownloadSavedResultRaw,
  classifySavedResultArtifact,
  projectSavedResults,
  resolveUncertainSavedResultSubmission,
  savedResultIdempotencyKey,
  savedResultSelectionKey,
  shouldApplySavedResultResponse,
} from './saved-results';

function artifact(overrides: Record<string, unknown> = {}) {
  return {
    artifactId: 'art_01TESTSAVEDRESULT01',
    contentType: 'text/markdown',
    sizeBytes: 128,
    status: 'COMPLETE',
    sha256: 'sha256:abc123',
    downloadUrl: null,
    defaultReadRef: null,
    rawAccessAllowed: true,
    metadata: {},
    links: [{ linkType: 'report.primary', label: 'Final report' }],
    ...overrides,
  };
}

function execution(overrides: Record<string, unknown> = {}) {
  return {
    workflowId: 'wf-4020',
    runId: 'run-selected-1',
    state: 'failed',
    closeStatus: null,
    finishSummary: null,
    outputBranch: null,
    ...overrides,
  };
}

describe('saved-result classification never invents a complete save', () => {
  it('marks a digest-backed COMPLETE artifact as complete', () => {
    expect(classifySavedResultArtifact(artifact()).complete).toBe(true);
  });

  it('treats absent digest, provider exit, or local directory as incomplete', () => {
    expect(
      classifySavedResultArtifact(artifact({ sha256: null })).complete,
    ).toBe(false);
    expect(
      classifySavedResultArtifact(
        artifact({ sha256: null, metadata: { providerExitCode: 0 } }),
      ).complete,
    ).toBe(false);
    expect(
      classifySavedResultArtifact(
        artifact({ sha256: null, metadata: { localDirectory: '/tmp/work' } }),
      ).complete,
    ).toBe(false);
    expect(
      classifySavedResultArtifact(artifact({ status: 'PENDING' })).complete,
    ).toBe(false);
  });

  it('does not treat permissive generic metadata as proof of completeness', () => {
    expect(
      classifySavedResultArtifact(
        artifact({
          sha256: null,
          sizeBytes: null,
          metadata: { redactionLevel: 'none', generic: true },
        }),
      ).complete,
    ).toBe(false);
  });
});

describe('separate compute/save/publication outcomes', () => {
  it('keeps a committed save visible when compute failed', () => {
    const projection = projectSavedResults({
      workflowId: 'wf-4020',
      runId: 'run-selected-1',
      execution: execution({ state: 'failed' }),
      artifacts: [artifact()],
      artifactsStale: false,
      artifactsError: null,
    });
    expect(projection.state).toBe('ready');
    expect(projection.computeOutcome).toBe('failed');
    expect(projection.saveOutcome).toBe('committed');
    expect(projection.entries).toHaveLength(1);
  });

  it('keeps a successful save when requested publication failed', () => {
    const projection = projectSavedResults({
      workflowId: 'wf-4020',
      runId: 'run-selected-1',
      execution: execution({
        state: 'failed',
        finishSummary: { publish: { status: 'failed' } },
      }),
      artifacts: [artifact()],
      artifactsStale: false,
      artifactsError: null,
    });
    expect(projection.saveOutcome).toBe('committed');
    expect(projection.publicationOutcome).toBe('failed');
  });

  it('shows unavailable/pending without inventing status when projection data is missing or stale', () => {
    const missing = projectSavedResults({
      workflowId: 'wf-4020',
      runId: 'run-selected-1',
      execution: execution(),
      artifacts: [],
      artifactsStale: false,
      artifactsError: new Error('Artifacts: unavailable'),
    });
    expect(missing.state).toBe('unavailable');
    expect(missing.saveOutcome).toBe('unavailable');

    const stale = projectSavedResults({
      workflowId: 'wf-4020',
      runId: 'run-selected-1',
      execution: execution(),
      artifacts: [artifact()],
      artifactsStale: true,
      artifactsError: null,
    });
    expect(stale.state).toBe('stale');
    expect(stale.entries).toHaveLength(0);
  });
});

describe('stale-response guard binds to the exact selected result', () => {
  it('ignores late responses for a historical selection', () => {
    const current = savedResultSelectionKey('wf-4020', 'run-new-2');
    expect(shouldApplySavedResultResponse('wf-4020|run-old-1', current)).toBe(
      false,
    );
    expect(shouldApplySavedResultResponse(current, current)).toBe(true);
  });
});

describe('uncertain submissions reuse idempotency and returned operation IDs', () => {
  it('produces a stable key per selected result and action', () => {
    const first = savedResultIdempotencyKey('wf-4020', 'run-1', 'continue');
    const second = savedResultIdempotencyKey('wf-4020', 'run-1', 'continue');
    expect(first).toBe(second);
    expect(savedResultIdempotencyKey('wf-4020', 'run-2', 'continue')).not.toBe(
      first,
    );
  });

  it('reuses the returned operation after a lost acknowledgment or double click', () => {
    const resolved = resolveUncertainSavedResultSubmission({
      pendingOperationId: 'op-continue-1',
      response: {
        status: 409,
        code: 'continuation_idempotency_conflict',
        operationId: 'op-continue-1',
      },
    });
    expect(resolved.reused).toBe(true);
    expect(resolved.operationId).toBe('op-continue-1');
  });

  it('binds continue requests to the exact selected source artifacts', () => {
    const body = buildContinueInNewWorkflowBody({
      idempotencyKey: savedResultIdempotencyKey('wf-4020', 'run-1', 'continue'),
      selectedSourceArtifactRefs: ['art_01TESTSAVEDRESULT01'],
      instructions: 'Continue working from the saved result.',
    });
    expect(body.selectedSourceArtifactRefs).toEqual([
      'art_01TESTSAVEDRESULT01',
    ]);
    expect(body).not.toHaveProperty('host');
    expect(body).not.toHaveProperty('sessionId');
    expect(body).not.toHaveProperty('credential');
  });
});

describe('denied and raw access honors preview-versus-raw policy', () => {
  it('hides raw download when raw access is denied and keeps preview inert', () => {
    const denied = artifact({ rawAccessAllowed: false });
    expect(canDownloadSavedResultRaw(denied)).toBe(false);
    const preview = buildSavedResultPreviewHref('/api', denied);
    const raw = buildSavedResultDownloadHref('/api', denied);
    expect(preview).not.toBe(raw);
  });

  it('resolves preview through default_read_ref without authorizing raw restore', () => {
    const withPreview = artifact({
      rawAccessAllowed: false,
      defaultReadRef: { artifactId: 'art_01PREVIEWREDACTED01' },
    });
    expect(buildSavedResultPreviewHref('/api', withPreview)).toBe(
      '/api/artifacts/art_01PREVIEWREDACTED01/download',
    );
    expect(canDownloadSavedResultRaw(withPreview)).toBe(false);
  });

  it('never treats an ArtifactRef as a URL or credential', () => {
    const ref = artifact({ artifactId: 'art_01TESTSAVEDRESULT01' });
    const href = buildSavedResultDownloadHref('/api', ref);
    expect(href.startsWith('/api/artifacts/')).toBe(true);
    expect(href).not.toContain('art_01TESTSAVEDRESULT01://');
  });
});
