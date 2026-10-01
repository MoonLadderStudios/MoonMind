import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  SavedWorkPublicationError,
  buildSavedResultDownloadHref,
  buildSavedResultPreviewHref,
  buildSavedWorkPublicationRequest,
  canDownloadSavedResultRaw,
  canPublishSavedWork,
  classifySavedResultArtifact,
  defaultSavedWorkDestination,
  projectSavedResults,
  publishSavedWork,
  savedResultContinuationKey,
  savedResultErrorMessage,
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

describe('uncertain submissions reuse idempotency through the authored intent', () => {
  const intent = {
    instructions: 'Continue from the saved report.',
    title: 'Follow-up',
    selectedSourceArtifactRefs: ['art-b', 'art-a'],
  };

  it('maps a double click, lost acknowledgment, or reload to one key', () => {
    const first = savedResultContinuationKey('wf-4020', 'run-1', intent);
    expect(savedResultContinuationKey('wf-4020', 'run-1', { ...intent })).toBe(first);
    expect(
      savedResultContinuationKey('wf-4020', 'run-1', {
        ...intent,
        selectedSourceArtifactRefs: ['art-a', 'art-b'],
      }),
    ).toBe(first);
    expect(first.length).toBeLessThanOrEqual(512);
  });

  it('treats a changed intent, ref set, or source run as a new request', () => {
    const first = savedResultContinuationKey('wf-4020', 'run-1', intent);
    expect(
      savedResultContinuationKey('wf-4020', 'run-1', { ...intent, instructions: 'Other work.' }),
    ).not.toBe(first);
    expect(
      savedResultContinuationKey('wf-4020', 'run-1', {
        ...intent,
        selectedSourceArtifactRefs: ['art-a'],
      }),
    ).not.toBe(first);
    expect(savedResultContinuationKey('wf-4020', 'run-2', intent)).not.toBe(first);
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

describe('server error messages', () => {
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

const SAVED_WORK = 'application/vnd.moonmind.saved-work-manifest+json;version=1';

function capture(overrides: { summary?: Record<string, unknown> | null } = {}) {
  const summary =
    overrides.summary === undefined
      ? {
          capture_id: 'step-1:capture',
          required_formats: ['full_snapshot'],
          outputs: [
            { format: 'full_snapshot', status: 'self_contained', artifact_id: 'art-archive' },
            { format: 'exact_baseline_delta', status: 'requires_dependencies', artifact_id: 'art-delta' },
            { format: 'selected_history', status: 'inapplicable' },
          ],
          exclusion_count: 3,
          exclusion_reasons: [
            { reason: 'sensitive-path-policy', count: 2 },
            { reason: 'sensitive-filename-policy', count: 1 },
          ],
          limitations: ['target-platform path/case collisions present'],
          retention_ref: 'artifact-ownership',
        }
      : overrides.summary;
  const part = (artifactId: string, kind: string, extra: Record<string, unknown> = {}) =>
    artifact({
      artifactId,
      contentType: 'application/octet-stream',
      metadata: { artifact_kind: kind },
      links: [{ linkType: 'output.checkpoint' }],
      ...extra,
    });
  return {
    manifest: artifact({
      artifactId: 'art-manifest',
      contentType: SAVED_WORK,
      metadata: {
        artifact_kind: 'saved_work_manifest',
        ...(summary ? { saved_work_summary: summary } : {}),
      },
      links: [{ linkType: 'output.checkpoint' }],
    }),
    archive: part('art-archive', 'checkpoint_archive'),
    delta: part('art-delta', 'checkpoint_delta'),
    fileManifest: part('art-file-manifest', 'checkpoint_manifest', {
      metadata: {
        artifact_kind: 'checkpoint_manifest',
        checkpoint_parts: {
          archive_artifact_id: 'art-archive',
          index_patch_artifact_id: 'art-index',
        },
      },
    }),
    index: part('art-index', 'checkpoint_index'),
    part,
  };
}

describe('the committed saved-work manifest is one saved unit', () => {
  it('groups the parts it names and surfaces the server summary', () => {
    const saved = capture();
    const projection = project({
      artifacts: [
        saved.manifest,
        saved.archive,
        saved.delta,
        saved.fileManifest,
        saved.index,
        artifact({ artifactId: 'art-report' }),
      ],
    });
    expect(projection.entries.map((entry) => entry.artifactId)).toEqual([
      'art-manifest',
      'art-report',
    ]);
    const [unit] = projection.entries;
    expect(unit?.kind).toBe('repository');
    expect(unit?.complete).toBe(true);
    expect(unit?.exclusions).toBe(3);
    expect(unit?.savedWork?.summaryAvailable).toBe(true);
    expect(unit?.savedWork?.limitations).toEqual([
      'target-platform path/case collisions present',
    ]);
    expect(unit?.savedWork?.parts.map((part) => [part.role, part.artifactId])).toEqual([
      ['snapshot', 'art-archive'],
      ['delta', 'art-delta'],
      ['file-manifest', 'art-file-manifest'],
      ['index-patch', 'art-index'],
    ]);
    expect(unit?.savedWork?.formats).toContainEqual({
      format: 'full_snapshot',
      status: 'self_contained',
      required: true,
    });
    expect(canPublishSavedWork(unit!)).toBe(true);
  });

  it('downgrades completeness when a required format or its part is not usable', () => {
    const saved = capture();
    const missingArchive = project({ artifacts: [saved.manifest, saved.delta] }).entries[0];
    expect(missingArchive?.complete).toBe(false);
    expect(missingArchive?.completenessReason).toBe('part-snapshot-not-listed');
    expect(canPublishSavedWork(missingArchive!)).toBe(false);

    const expiredArchive = project({
      now: Date.parse('2026-09-30T00:00:00Z'),
      artifacts: [
        saved.manifest,
        saved.part('art-archive', 'checkpoint_archive', { expiresAt: '2026-09-29T00:00:00Z' }),
      ],
    }).entries[0];
    expect(expiredArchive?.completenessReason).toBe('part-snapshot-expired');

    const failedFormat = project({
      artifacts: [
        capture({
          summary: {
            required_formats: ['full_snapshot'],
            outputs: [{ format: 'full_snapshot', status: 'incomplete' }],
          },
        }).manifest,
      ],
    }).entries[0];
    expect(failedFormat?.complete).toBe(false);
    expect(failedFormat?.completenessReason).toBe('format-full_snapshot-incomplete');
  });

  it('never upgrades a manifest the server has not completed', () => {
    const saved = capture();
    const pending = project({
      artifacts: [{ ...saved.manifest, status: 'PENDING_UPLOAD', sha256: null }, saved.archive],
    }).entries[0];
    expect(pending?.complete).toBe(false);
    expect(pending?.completenessReason).toBe('status-PENDING_UPLOAD');
    expect(canPublishSavedWork(pending!)).toBe(false);
  });

  it('keeps a manifest listed without a summary useful but honest', () => {
    const legacy = project({ artifacts: [capture({ summary: null }).manifest] }).entries[0];
    expect(legacy?.savedWork?.summaryAvailable).toBe(false);
    expect(legacy?.exclusions).toBeNull();
    expect(legacy?.kind).toBe('repository');
  });

  it('requires raw access to the manifest before publication is offered', () => {
    const restricted = project({
      artifacts: [{ ...capture().manifest, rawAccessAllowed: false }, capture().archive],
    }).entries[0];
    expect(restricted?.complete).toBe(true);
    expect(restricted?.restricted).toBe(true);
    expect(canPublishSavedWork(restricted!)).toBe(false);
  });
});

describe('Publish Saved Work uses the saved-work publication contract', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('derives the destination the execution already determines', () => {
    expect(
      defaultSavedWorkDestination(
        execution({ repository: 'Owner/Repo', startingBranch: 'main', publishMode: 'branch' }),
        'mm:wf 4020',
      ),
    ).toEqual({
      repository: 'Owner/Repo',
      objective: 'branch',
      baseBranch: 'main',
      headBranch: 'saved-work/mm-wf-4020',
      strategy: 'baseline_delta',
      pullRequestTitle: '',
    });
  });

  it('authors only the saved result and its destination', () => {
    const draft = {
      repository: ' Owner/Repo ',
      objective: 'pr' as const,
      baseBranch: '',
      headBranch: ' saved-work/x ',
      strategy: 'additive_import' as const,
      pullRequestTitle: ' Publish it ',
    };
    expect(buildSavedWorkPublicationRequest('art-manifest', draft)).toEqual({
      savedWorkRef: 'art-manifest',
      destination: {
        repository: 'Owner/Repo',
        objective: 'pr',
        headBranch: 'saved-work/x',
        strategy: 'additive_import',
      },
      pullRequestTitle: 'Publish it',
    });
    expect(
      buildSavedWorkPublicationRequest('art-manifest', { ...draft, objective: 'branch' }),
    ).not.toHaveProperty('pullRequestTitle');
  });

  it('posts the body and distinguishes a server rejection from a lost acknowledgment', async () => {
    const request = buildSavedWorkPublicationRequest('art-manifest', {
      repository: 'Owner/Repo',
      objective: 'pr',
      baseBranch: 'main',
      headBranch: 'saved-work/x',
      strategy: 'baseline_delta',
      pullRequestTitle: '',
    });
    const fetchSpy = vi.spyOn(window, 'fetch');
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: {
            code: 'publication_retry_not_admitted',
            message: 'Publication is not admitted by current rollout policy.',
          },
        }),
        { status: 409 },
      ),
    );
    const rejected = await publishSavedWork('/api', 'mm:wf', request).catch((err) => err);
    expect(rejected).toBeInstanceOf(SavedWorkPublicationError);
    expect(rejected.code).toBe('publication_retry_not_admitted');
    expect(rejected.message).toBe('Publication is not admitted by current rollout policy.');
    const [url, init] = fetchSpy.mock.calls[0]!;
    expect(url).toBe('/api/executions/mm%3Awf/retry-publication');
    expect(JSON.parse(String(init?.body))).toEqual(request);

    fetchSpy.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const lost = await publishSavedWork('/api', 'mm:wf', request).catch((err) => err);
    expect(lost).toBeInstanceOf(Error);
    expect(lost).not.toBeInstanceOf(SavedWorkPublicationError);

    fetchSpy.mockResolvedValueOnce(new Response('<html>proxy</html>', { status: 201 }));
    const unreadable = await publishSavedWork('/api', 'mm:wf', request).catch((err) => err);
    expect(unreadable).toBeInstanceOf(Error);
    expect(unreadable).not.toBeInstanceOf(SavedWorkPublicationError);
  });
});
