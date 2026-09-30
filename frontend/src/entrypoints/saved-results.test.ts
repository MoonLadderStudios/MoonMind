import { describe, expect, it } from 'vitest';
import { apiErrorMessage } from '../features/workflow-native-chat/WorkflowTerminalChatActions';
import {
  buildSavedResultDownloadHref,
  buildSavedResultPreviewHref,
  canDownloadSavedResultRaw,
  classifySavedResultArtifact,
  projectSavedResults,
  savedResultContinueIdempotencyKey,
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

describe('uncertain submissions reuse idempotency', () => {
  const intent = {
    workflowId: 'wf-4020',
    runId: 'run-1',
    title: '',
    instructions: 'Finish the report.',
    selectedSourceArtifactRefs: ['art-b', 'art-a'],
  };

  it('resubmits the same key for the same selected run and authored request', () => {
    const first = savedResultContinueIdempotencyKey(intent);
    expect(savedResultContinueIdempotencyKey({ ...intent })).toBe(first);
    expect(
      savedResultContinueIdempotencyKey({
        ...intent,
        instructions: '  Finish the report.  ',
        selectedSourceArtifactRefs: ['art-a', 'art-b'],
      }),
    ).toBe(first);
    expect(first.length).toBeLessThanOrEqual(512);
  });

  it('uses a new key when the run or the authored request changes', () => {
    const first = savedResultContinueIdempotencyKey(intent);
    expect(savedResultContinueIdempotencyKey({ ...intent, runId: 'run-2' })).not.toBe(first);
    expect(
      savedResultContinueIdempotencyKey({ ...intent, instructions: 'Something else.' }),
    ).not.toBe(first);
    expect(
      savedResultContinueIdempotencyKey({ ...intent, selectedSourceArtifactRefs: [] }),
    ).not.toBe(first);
  });
});

describe('denied and raw access honors preview-versus-raw policy', () => {
  it('offers no preview when raw access is denied and no distinct preview exists', () => {
    const denied = artifact({ rawAccessAllowed: false });
    expect(canDownloadSavedResultRaw(denied)).toBe(false);
    expect(buildSavedResultPreviewHref('/api', denied)).toBeNull();
    // The server points default_read_ref at the raw artifact itself when no
    // preview exists; that download is refused, so it is not a preview.
    const selfRead = artifact({
      rawAccessAllowed: false,
      default_read_ref: { artifact_id: 'art_01TESTSAVEDRESULT01' },
    });
    expect(buildSavedResultPreviewHref('/api', selfRead)).toBeNull();
  });

  it('uses the server preview artifact ref in the real API shape', () => {
    const restricted = artifact({
      rawAccessAllowed: false,
      default_read_ref: { artifact_id: 'art_01PREVIEW02' },
      preview_artifact_ref: { artifact_id: 'art_01PREVIEW02' },
    });
    expect(buildSavedResultPreviewHref('/api', restricted)).toBe(
      '/api/artifacts/art_01PREVIEW02/download',
    );
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
      capturedEvidence: evidence(['art-output', 'art-partial', 'art-final-snapshot']),
    });
    expect(projection.continuationRefs).toEqual(['art-output']);
    expect(project().continuationRefs).toEqual([]);
  });

  it('matches artifact:// refs and never carries restricted or expired outputs', () => {
    const projection = project({
      now: Date.parse('2026-09-30T00:00:00Z'),
      artifacts: [
        artifact({ artifactId: 'art-output', links: [{ linkType: 'output.primary' }] }),
        artifact({ artifactId: 'art-restricted', rawAccessAllowed: false }),
        artifact({ artifactId: 'art-expired', expires_at: '2026-09-01T00:00:00Z' }),
      ],
      capturedEvidence: evidence([
        'artifact://art-output',
        'art-restricted',
        'art-expired',
      ]),
    });
    // The authorized ref string is submitted unchanged.
    expect(projection.continuationRefs).toEqual(['artifact://art-output']);
  });

  it('never applies captured evidence recorded for a different run', () => {
    const projection = project({
      capturedEvidence: {
        ...evidence(['art_01TESTSAVEDRESULT01']),
        runId: 'run-newer-2',
      },
    });
    expect(projection.continuationRefs).toEqual([]);
    expect(projection.capture.state).toBe('stale');
  });
});

function evidence(refs: string[], extra: Record<string, unknown> = {}) {
  return {
    workflowId: 'wf-4020',
    runId: 'run-selected-1',
    available: true,
    items: refs.map((ref) => ({ label: 'Output artifact', kind: 'output_artifact', artifactRef: ref })),
    ...extra,
  };
}

describe('save outcome comes from server evidence', () => {
  it('shows a recorded save failure and preserved workspace instead of a browser label', () => {
    const projection = project({
      execution: execution({
        state: 'failed',
        finishSummary: {
          controlStop: {
            auxiliaryOutcomes: {
              evidencePublication: { status: 'failed' },
              workspacePreservation: { status: 'preserved' },
              gitPublication: { status: 'not_attempted' },
              hostCleanup: { status: 'pending' },
            },
          },
        },
      }),
    });
    expect(projection.saveOutcome).toBe('failed');
    expect(projection.committedCount).toBe(1);
    expect(projection.evidencePublicationOutcome).toBe('failed');
    expect(projection.workspacePreservationOutcome).toBe('preserved');
    expect(projection.computeOutcome).toBe('failed');
    expect(projection.publicationOutcome).toBe('not_attempted');
    expect(projection.cleanupOutcome).toBe('pending');
  });

  it('reports capture-manifest completeness from the captured-evidence projection', () => {
    const recorded = project({
      capturedEvidence: {
        ...evidence(['art-output']),
        items: [
          { label: 'Capture manifest', kind: 'capture_manifest', artifactRef: 'art-manifest' },
          { label: 'Output artifact', kind: 'output_artifact', artifactRef: 'art-output' },
        ],
      },
    });
    expect(recorded.capture).toEqual({
      state: 'recorded',
      itemCount: 2,
      captureManifestRef: 'art-manifest',
      reason: null,
    });
    const unavailable = project({
      capturedEvidence: {
        ...evidence([]),
        available: false,
        unavailableReason: 'no_terminal_artifacts_captured',
      },
    });
    expect(unavailable.capture.state).toBe('unavailable');
    expect(unavailable.capture.reason).toBe('no_terminal_artifacts_captured');
    expect(project({ capturedEvidenceError: true }).capture.state).toBe('unavailable');
    expect(project().capture.state).toBe('pending');
  });

  it('keeps separate outcomes when saved-result evidence is unavailable', () => {
    const projection = project({
      execution: execution({
        state: 'canceled',
        finishSummary: {
          controlStop: { auxiliaryOutcomes: { gitPublication: { status: 'failed' } } },
        },
      }),
      artifactsError: new Error('Artifacts: unavailable'),
    });
    expect(projection.state).toBe('unavailable');
    expect(projection.computeOutcome).toBe('canceled');
    expect(projection.publicationOutcome).toBe('failed');
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
  it('preserves structured FastAPI error messages', () => {
    expect(
      apiErrorMessage({
        detail: { code: 'continuation_source_not_terminal', message: 'Source is not terminal.' },
      }),
    ).toBe('Source is not terminal.');
    expect(apiErrorMessage({ detail: 'plain detail' })).toBe('plain detail');
    expect(apiErrorMessage({ message: 'top-level' })).toBe('top-level');
    expect(apiErrorMessage(null)).toBe('');
  });
});
