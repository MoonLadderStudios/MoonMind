import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor, within } from '../../utils/test-utils';
import { renderWithClient } from '../../utils/test-utils';
import { OperationsSettingsSection } from './OperationsSettingsSection';

const workerSnapshot = {
  system: {
    workersPaused: false,
    mode: 'running',
    version: 'test',
    updatedAt: '2026-04-26T00:00:00Z',
    reason: 'Normal operation',
  },
  metrics: {
    queued: 1,
    running: 2,
    staleRunning: 0,
    isDrained: true,
  },
  audit: { latest: [] },
};

const stackState = {
  stack: 'moonmind',
  projectName: 'moonmind',
  configuredImage: 'ghcr.io/moonladderstudios/moonmind:stable',
  version: '2026.04.25',
  runningImages: [
    {
      service: 'api',
      image: 'ghcr.io/moonladderstudios/moonmind:stable',
      imageId: 'sha256:api-image',
      digest: 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    },
  ],
  services: [
    { name: 'api', state: 'running', health: 'healthy' },
    { name: 'worker', state: 'running', health: 'healthy' },
  ],
  lastUpdateRunId: 'depupd_recent',
  recentActions: [
    {
      status: 'SUCCEEDED',
      requestedImage: 'ghcr.io/moonladderstudios/moonmind:20260425.1234',
      resolvedDigest: 'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
      operator: 'admin@example.com',
      reason: 'Routine release',
      startedAt: '2026-04-25T18:00:00Z',
      completedAt: '2026-04-25T18:04:00Z',
      runDetailUrl: '/tasks/depupd_recent',
      logsArtifactUrl: '/api/artifacts/logs',
      rawCommandLogUrl: null,
      beforeSummary: 'stable',
      afterSummary: '20260425.1234',
    },
  ],
};

const stackStateWithRollback = {
  ...stackState,
  recentActions: [
    {
      status: 'FAILED',
      requestedImage: 'ghcr.io/moonladderstudios/moonmind:20260425.1234',
      operator: 'admin@example.com',
      reason: 'Routine release failed',
      startedAt: '2026-04-25T18:00:00Z',
      completedAt: '2026-04-25T18:04:00Z',
      runDetailUrl: '/tasks/depupd_recent',
      logsArtifactUrl: '/api/artifacts/logs',
      beforeSummary: 'ghcr.io/moonladderstudios/moonmind:stable',
      afterSummary: 'verification failed',
      rollbackEligibility: {
        eligible: true,
        sourceActionId: 'depupd_recent',
        targetImage: {
          repository: 'ghcr.io/moonladderstudios/moonmind',
          reference: 'stable',
        },
        evidenceRef: 'art:sha256:before',
      },
    },
    {
      status: 'FAILED',
      requestedImage: 'ghcr.io/moonladderstudios/moonmind:latest',
      operator: 'admin@example.com',
      reason: 'Unsafe rollback evidence',
      startedAt: '2026-04-25T19:00:00Z',
      completedAt: '2026-04-25T19:04:00Z',
      rollbackEligibility: {
        eligible: false,
        sourceActionId: 'depupd_unsafe',
        targetImage: null,
        reason: 'Before-state evidence is missing.',
      },
    },
  ],
};

const imageTargets = {
  stack: 'moonmind',
  repositories: [
    {
      repository: 'ghcr.io/moonladderstudios/moonmind',
      allowedReferences: ['stable', 'latest'],
      recentTags: ['20260425.1234'],
      digestPinningRecommended: true,
      allowedModes: ['changed_services', 'force_recreate'],
    },
  ],
};

describe('OperationsSettingsSection deployment update card', () => {
  let fetchSpy: MockInstance;
  let confirmSpy: MockInstance;

  beforeEach(() => {
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input) => {
      const url = String(input);
      if (url === '/api/workers') {
        return Promise.resolve({
          ok: true,
          json: async () => workerSnapshot,
        } as Response);
      }
      if (url === '/api/worker-action') {
        return Promise.resolve({
          ok: true,
          json: async () => workerSnapshot,
        } as Response);
      }
      if (url === '/api/v1/operations/deployment/stacks/moonmind') {
        return Promise.resolve({
          ok: true,
          json: async () => stackState,
        } as Response);
      }
      if (url === '/api/v1/operations/deployment/image-targets?stack=moonmind') {
        return Promise.resolve({
          ok: true,
          json: async () => imageTargets,
        } as Response);
      }
      if (url === '/api/v1/operations/deployment/update') {
        return Promise.resolve({
          ok: true,
          status: 202,
          json: async () => ({
            deploymentUpdateRunId: 'depupd_queued',
            taskId: 'mm:deployment-update',
            workflowId: 'mm:deployment-update',
            status: 'QUEUED',
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: `Unhandled ${url}`,
        json: async () => ({}),
      } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    confirmSpy.mockRestore();
  });

  function renderOperations() {
    renderWithClient(
      <OperationsSettingsSection
        workerPauseConfig={{
          get: '/api/workers',
          post: '/api/worker-action',
          pollIntervalMs: 60_000,
        }}
      />,
    );
  }

  it('confirms worker pause and submits operation command metadata', async () => {
    renderOperations();

    const workerCard = await screen.findByRole('region', { name: /worker operations/i });
    await within(workerCard).findByRole('button', { name: /pause workers/i });
    fireEvent.change(within(workerCard).getAllByLabelText(/^reason$/i)[0], {
      target: { value: 'Maintenance window' },
    });
    fireEvent.click(within(workerCard).getByRole('button', { name: /pause workers/i }));

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Pause workers?'));
      const workerCall = fetchSpy.mock.calls.find(
        ([url]) => String(url) === '/api/worker-action',
      );
      expect(workerCall).toBeDefined();
      expect(JSON.parse(String(workerCall?.[1]?.body))).toMatchObject({
        action: 'pause',
        mode: 'drain',
        reason: 'Maintenance window',
        confirmation: expect.stringContaining('Pause workers confirmed'),
      });
    });
  });

  it('renders deployment state inside Operations without top-level deployment navigation', async () => {
    renderOperations();

    const card = await screen.findByRole('region', { name: /deployment update/i });
    await within(card).findByText('ghcr.io/moonladderstudios/moonmind:stable');
    expect(within(card).getAllByText('moonmind').length).toBeGreaterThanOrEqual(2);
    expect(within(card).getByText('ghcr.io/moonladderstudios/moonmind:stable')).toBeTruthy();
    expect(within(card).getByText(/sha256:api-image/i)).toBeTruthy();
    expect(within(card).getByText(/healthy/i)).toBeTruthy();
    expect(within(card).getByText(/depupd_recent/i)).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: /deployment/i })).toBeNull();
  });

  it('prefers recent release tags, warns for mutable tags, and omits runner image controls', async () => {
    renderOperations();

    const card = await screen.findByRole('region', { name: /deployment update/i });
    const reference = (await within(card).findByLabelText(/target reference/i)) as HTMLSelectElement;
    expect(reference.value).toBe('20260425.1234');

    fireEvent.change(reference, { target: { value: 'latest' } });
    expect(within(card).getByText(/latest may resolve differently/i)).toBeTruthy();

    const mode = within(card).getByLabelText(/update mode/i) as HTMLSelectElement;
    expect(mode.value).toBe('changed_services');
    expect(within(mode).getByRole('option', { name: /force recreate all services/i })).toBeTruthy();
    expect(within(card).getByText(/recreate every service/i)).toBeTruthy();
    expect(within(card).queryByLabelText(/updater runner/i)).toBeNull();
  });

  it('defaults missing deployment mode policy to changed services only', async () => {
    fetchSpy.mockImplementation((input) => {
      const url = String(input);
      if (url === '/api/workers') {
        return Promise.resolve({
          ok: true,
          json: async () => workerSnapshot,
        } as Response);
      }
      if (url === '/api/v1/operations/deployment/stacks/moonmind') {
        return Promise.resolve({
          ok: true,
          json: async () => stackState,
        } as Response);
      }
      if (url === '/api/v1/operations/deployment/image-targets?stack=moonmind') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...imageTargets,
            repositories: imageTargets.repositories.map(({ allowedModes, ...repository }) => repository),
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: `Unhandled ${url}`,
        json: async () => ({}),
      } as Response);
    });

    renderOperations();

    const card = await screen.findByRole('region', { name: /deployment update/i });
    const mode = await within(card).findByLabelText(/update mode/i);
    expect(within(mode).getByRole('option', { name: /restart changed services/i })).toBeTruthy();
    expect(within(mode).queryByRole('option', { name: /force recreate all services/i })).toBeNull();
    expect(within(card).queryByText(/recreate every service/i)).toBeNull();
  });

  it('submits the typed deployment payload without requiring a reason', async () => {
    renderOperations();

    const card = await screen.findByRole('region', { name: /deployment update/i });

    fireEvent.change(await within(card).findByLabelText(/target reference/i), {
      target: { value: 'latest' },
    });
    fireEvent.click(
      await within(card).findByRole('button', { name: /submit deployment update/i }),
    );

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Current image: ghcr.io/moonladderstudios/moonmind:stable'));
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Target image: ghcr.io/moonladderstudios/moonmind:latest'));
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Mode: Restart changed services'));
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Stack: moonmind'));
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Expected affected services: api, worker'));
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Mutable tag warning'));
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Services may restart'));
    });

    await waitFor(() => {
      const updateCall = fetchSpy.mock.calls.find(([url]) => String(url) === '/api/v1/operations/deployment/update');
      expect(updateCall).toBeDefined();
      expect(JSON.parse(String(updateCall?.[1]?.body))).toMatchObject({
        stack: 'moonmind',
        image: {
          repository: 'ghcr.io/moonladderstudios/moonmind',
          reference: 'latest',
        },
        mode: 'changed_services',
        removeOrphans: true,
        wait: true,
        runSmokeCheck: false,
        pauseWork: false,
        pruneOldImages: false,
      });
      expect(JSON.parse(String(updateCall?.[1]?.body))).not.toHaveProperty('reason');
    });
    expect(await within(card).findByText(/deployment update queued/i)).toBeTruthy();
  });

  it('renders recent deployment context and hides raw command-log links by default', async () => {
    renderOperations();

    const card = await screen.findByRole('region', { name: /deployment update/i });
    await within(card).findByText('SUCCEEDED');
    expect(within(card).getByText('SUCCEEDED')).toBeTruthy();
    expect(within(card).getByText(/Routine release/i)).toBeTruthy();
    expect(within(card).getByText(/admin@example.com/i)).toBeTruthy();
    expect(within(card).getByRole('link', { name: /run detail/i }).getAttribute('href')).toBe(
      '/tasks/depupd_recent',
    );
    expect(within(card).getByRole('link', { name: /logs artifact/i }).getAttribute('href')).toBe(
      '/api/artifacts/logs',
    );
    expect(within(card).queryByRole('link', { name: /raw command/i })).toBeNull();
  });

  it('renders rollback only for eligible recent deployment actions', async () => {
    fetchSpy.mockImplementation((input) => {
      const url = String(input);
      if (url === '/api/workers') {
        return Promise.resolve({ ok: true, json: async () => workerSnapshot } as Response);
      }
      if (url === '/api/v1/operations/deployment/stacks/moonmind') {
        return Promise.resolve({
          ok: true,
          json: async () => stackStateWithRollback,
        } as Response);
      }
      if (url === '/api/v1/operations/deployment/image-targets?stack=moonmind') {
        return Promise.resolve({ ok: true, json: async () => imageTargets } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) } as Response);
    });

    renderOperations();

    const card = await screen.findByRole('region', { name: /deployment update/i });
    expect(await within(card).findByRole('button', { name: /roll back to stable/i })).toBeTruthy();
    expect(within(card).queryByRole('button', { name: /roll back to latest/i })).toBeNull();
    expect(within(card).getByText(/before-state evidence is missing/i)).toBeTruthy();
  });

  it('confirms rollback and submits the typed deployment rollback payload', async () => {
    fetchSpy.mockImplementation((input, init) => {
      const url = String(input);
      if (url === '/api/workers') {
        return Promise.resolve({ ok: true, json: async () => workerSnapshot } as Response);
      }
      if (url === '/api/v1/operations/deployment/stacks/moonmind') {
        return Promise.resolve({
          ok: true,
          json: async () => stackStateWithRollback,
        } as Response);
      }
      if (url === '/api/v1/operations/deployment/image-targets?stack=moonmind') {
        return Promise.resolve({ ok: true, json: async () => imageTargets } as Response);
      }
      if (url === '/api/v1/operations/deployment/update') {
        return Promise.resolve({
          ok: true,
          status: 202,
          json: async () => ({
            deploymentUpdateRunId: 'depupd_rollback',
            taskId: 'mm:deployment-update',
            workflowId: 'mm:deployment-update',
            status: 'QUEUED',
            body: init?.body,
          }),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) } as Response);
    });

    renderOperations();

    const card = await screen.findByRole('region', { name: /deployment update/i });
    fireEvent.click(await within(card).findByRole('button', { name: /roll back to stable/i }));

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Rollback deployment?'));
      expect(confirmSpy).toHaveBeenCalledWith(
        expect.stringContaining('Target image: ghcr.io/moonladderstudios/moonmind:stable'),
      );
    });

    await waitFor(() => {
      const updateCall = fetchSpy.mock.calls.find(([url]) => String(url) === '/api/v1/operations/deployment/update');
      expect(updateCall).toBeDefined();
      expect(JSON.parse(String(updateCall?.[1]?.body))).toMatchObject({
        stack: 'moonmind',
        image: {
          repository: 'ghcr.io/moonladderstudios/moonmind',
          reference: 'stable',
        },
        operationKind: 'rollback',
        rollbackSourceActionId: 'depupd_recent',
        confirmation: expect.stringContaining('Rollback to ghcr.io/moonladderstudios/moonmind:stable confirmed'),
        reason: expect.stringContaining('Rollback after failed update depupd_recent'),
      });
    });
    expect(await within(card).findByText(/deployment rollback queued/i)).toBeTruthy();
  });
});
