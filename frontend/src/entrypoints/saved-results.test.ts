import { describe, expect, it } from 'vitest';
import {
  buildContinueInNewWorkflowBody,
  buildSavedResultDownloadHref,
  buildSavedResultPreviewHref,
  canDownloadSavedResultRaw,
  classifySavedResultArtifact,
  projectSavedResults,
  resolveUncertainSavedResultSubmission,
  savedResultErrorMessage,
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

  it('rejects the absent digest sentinel stored in metadata', () => {
    const classified = classifySavedResultArtifact(
      artifact({ sha256: null, metadata: { digest: 'absent' } }),
    );
    expect(classified.complete).toBe(false);
    expect(classified.reason).toBe('absent-digest');
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

function project(overrides: Partial<Parameters<typeof projectSavedResults>[0]> = {}) {
  return projectSavedResults({
    workflowId: 'wf-4020',
    runId: 'run-selected-1',
    execution: execution(),
    artifacts: [artifact()],
    artifactsStale: false,
    artifactsError: null,
    ...overrides,
  });
}

describe('saved outputs come only from canonical result artifacts', () => {
  it('excludes inputs, runtime logs, and debug evidence from saved outputs', () => {
    const projection = project({
      artifacts: [
        artifact({ artifactId: 'art-report' }),
        artifact({ artifactId: 'art-input', links: [{ linkType: 'input.instructions' }] }),
        artifact({ artifactId: 'art-stdout', links: [{ linkType: 'runtime.stdout' }] }),
        artifact({ artifactId: 'art-logs', links: [{ linkType: 'output.logs' }] }),
        artifact({ artifactId: 'art-debug', links: [{ linkType: 'debug.trace' }] }),
        artifact({ artifactId: 'art-unlinked', links: [] }),
        artifact({ artifactId: 'art-output', links: [{ linkType: 'output.primary' }] }),
      ],
    });
    expect(projection.entries.map((entry) => entry.artifactId)).toEqual([
      'art-report',
      'art-output',
    ]);
  });

  it('submits only complete entries the server authorizes for continuation', () => {
    const projection = project({
      artifacts: [
        artifact({ artifactId: 'art-report' }),
        artifact({ artifactId: 'art-output', links: [{ linkType: 'output.primary' }] }),
        artifact({ artifactId: 'art-partial', status: 'PENDING_UPLOAD' }),
      ],
      authorizedContinuationRefs: ['art-output', 'art-partial', 'art-final-snapshot'],
    });
    expect(projection.continuationRefs).toEqual(['art-output']);
    expect(project().continuationRefs).toEqual([]);
  });
});

describe('terminal source and outcome evidence', () => {
  it('treats no-commit executions as terminal', () => {
    const projection = project({ execution: execution({ state: 'no_commit' }) });
    expect(projection.computeOutcome).toBe('no_commit');
    expect(projection.terminalSource).toBe(true);
    expect(project({ execution: execution({ state: 'executing' }) }).terminalSource).toBe(false);
  });

  it('reads the authoritative publication recovery outcome', () => {
    const projection = project({
      execution: execution({
        finishSummary: {
          controlStop: { auxiliaryOutcomes: { gitPublication: { status: 'failed' } } },
        },
        outputBranch: { status: 'pushed' },
      }),
    });
    expect(projection.publicationOutcome).toBe('failed');
  });

  it('reports the recorded cleanup outcome instead of assuming preservation', () => {
    const cleanup = (auxiliaryOutcomes: Record<string, unknown>) =>
      project({
        execution: execution({ finishSummary: { controlStop: { auxiliaryOutcomes } } }),
      }).cleanupOutcome;
    expect(project().cleanupOutcome).toBe('unknown');
    expect(
      cleanup({
        hostCleanup: { status: 'failed' },
        providerProfileRelease: { status: 'completed' },
        janitorRequired: true,
      }),
    ).toBe('failed');
    expect(
      cleanup({
        hostCleanup: { status: 'pending' },
        providerProfileRelease: { status: 'pending' },
        janitorRequired: false,
      }),
    ).toBe('pending');
    expect(
      cleanup({
        hostCleanup: { status: 'completed' },
        providerProfileRelease: { status: 'completed' },
        janitorRequired: false,
      }),
    ).toBe('completed');
    expect(
      project({ execution: execution(), artifactsError: new Error('x') }).cleanupOutcome,
    ).toBe('unknown');
  });
});

describe('expired saved outputs', () => {
  it('derives expiration from the server expiry timestamp', () => {
    const now = Date.parse('2026-09-30T00:00:00Z');
    const projection = project({
      now,
      artifacts: [
        artifact({ artifactId: 'art-old', expiresAt: '2026-09-29T00:00:00Z' }),
        artifact({ artifactId: 'art-new', expires_at: '2026-10-01T00:00:00Z' }),
        artifact({ artifactId: 'art-forever' }),
      ],
    });
    expect(projection.entries.map((entry) => [entry.artifactId, entry.expired])).toEqual([
      ['art-old', true],
      ['art-new', false],
      ['art-forever', false],
    ]);
  });
});

describe('continuation responses', () => {
  it('treats an idempotency conflict without a destination as a failure', () => {
    const resolved = resolveUncertainSavedResultSubmission({
      pendingOperationId: 'op-continue-1',
      response: { status: 409, code: 'continuation_idempotency_conflict', operationId: null },
    });
    expect(resolved.reused).toBe(false);
  });

  it('preserves structured FastAPI error messages', () => {
    expect(
      savedResultErrorMessage({
        detail: { code: 'continuation_source_not_terminal', message: 'Source is not terminal.' },
      }),
    ).toBe('Source is not terminal.');
    expect(savedResultErrorMessage({ detail: 'plain detail' })).toBe('plain detail');
    expect(savedResultErrorMessage({ message: 'top-level' })).toBe('top-level');
    expect(savedResultErrorMessage(null)).toBe('');
  });
});
