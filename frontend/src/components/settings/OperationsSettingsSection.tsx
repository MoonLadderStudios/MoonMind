import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';

const WorkerSnapshotSchema = z.object({
  system: z
    .object({
      workersPaused: z.boolean().optional(),
      mode: z.string().optional(),
      version: z.string().optional(),
      updatedAt: z.string().optional(),
      reason: z.string().optional(),
    })
    .optional(),
  metrics: z
    .object({
      queued: z.number().optional(),
      running: z.number().optional(),
      staleRunning: z.number().optional(),
      isDrained: z.boolean().optional(),
    })
    .optional(),
  audit: z
    .object({
      latest: z
        .array(
          z.object({
            action: z.string().optional(),
            mode: z.string().optional(),
            reason: z.string().optional(),
            createdAt: z.string().optional(),
          }),
        )
        .optional(),
    })
    .optional(),
});

type WorkerSnapshot = z.infer<typeof WorkerSnapshotSchema>;

const DeploymentStackStateSchema = z
  .object({
    stack: z.string(),
    projectName: z.string(),
    configuredImage: z.string(),
    version: z.string().optional().nullable(),
    build: z.string().optional().nullable(),
    runningImages: z
      .array(
        z
          .object({
            service: z.string(),
            image: z.string(),
            imageId: z.string().optional().nullable(),
            digest: z.string().optional().nullable(),
          })
          .passthrough(),
      )
      .default([]),
    services: z
      .array(
        z
          .object({
            name: z.string(),
            state: z.string(),
            health: z.string().optional().nullable(),
          })
          .passthrough(),
      )
      .default([]),
    lastUpdateRunId: z.string().optional().nullable(),
    recentActions: z
      .array(
        z
          .object({
            status: z.string().optional().nullable(),
            requestedImage: z.string().optional().nullable(),
            resolvedDigest: z.string().optional().nullable(),
            operator: z.string().optional().nullable(),
            reason: z.string().optional().nullable(),
            startedAt: z.string().optional().nullable(),
            completedAt: z.string().optional().nullable(),
            runDetailUrl: z.string().optional().nullable(),
            logsArtifactUrl: z.string().optional().nullable(),
            rawCommandLogUrl: z.string().optional().nullable(),
            rawCommandLogPermitted: z.boolean().optional(),
            id: z.union([z.string(), z.number()]).optional().nullable(),
            runId: z.string().optional().nullable(),
            beforeSummary: z.string().optional().nullable(),
            afterSummary: z.string().optional().nullable(),
            rollbackEligibility: z
              .object({
                eligible: z.boolean(),
                sourceActionId: z.string().optional().nullable(),
                targetImage: z
                  .object({
                    repository: z.string(),
                    reference: z.string(),
                  })
                  .optional()
                  .nullable(),
                reason: z.string().optional().nullable(),
                evidenceRef: z.string().optional().nullable(),
              })
              .optional()
              .nullable(),
          })
          .passthrough(),
      )
      .optional()
      .default([]),
  })
  .passthrough();

const ImageTargetsSchema = z
  .object({
    stack: z.string(),
    repositories: z.array(
      z
        .object({
          repository: z.string(),
          allowedReferences: z.array(z.string()).default([]),
          recentTags: z.array(z.string()).default([]),
          digestPinningRecommended: z.boolean().default(false),
          allowedModes: z.array(z.string()).optional(),
        })
        .passthrough(),
    ),
  })
  .passthrough();

type DeploymentStackState = z.infer<typeof DeploymentStackStateSchema>;
type ImageTargets = z.infer<typeof ImageTargetsSchema>;
type ImageTargetRepository = ImageTargets['repositories'][number];
type DeploymentAction = DeploymentStackState['recentActions'][number];

export interface WorkerPauseConfig {
  get: string;
  post: string;
  pollIntervalMs?: number;
}

const DEPLOYMENT_STACK = 'moonmind';

function uniqueStrings(values: string[]): string[] {
  return values.filter((value, index) => value && values.indexOf(value) === index);
}

function isMutableReference(reference: string): boolean {
  const normalized = reference.trim().toLowerCase();
  return normalized === 'latest' || normalized === 'stable';
}

function preferredReference(repository: ImageTargetRepository | undefined): string {
  if (!repository) {
    return '';
  }
  const digest = repository.allowedReferences.find((reference) =>
    reference.startsWith('sha256:'),
  );
  return digest || repository.recentTags[0] || repository.allowedReferences[0] || '';
}

function modeLabel(mode: string): string {
  if (mode === 'force_recreate') {
    return 'Force recreate all services';
  }
  return 'Restart changed services';
}

function modeDescription(mode: string): string {
  if (mode === 'force_recreate') {
    return 'Pull images and recreate every service in the allowlisted stack.';
  }
  return 'Pull images and recreate services whose image or configuration changed.';
}

function affectedServices(state: DeploymentStackState | undefined): string {
  const names = state?.services.map((service) => service.name).filter(Boolean) ?? [];
  return names.length > 0 ? names.join(', ') : 'services reported by deployment policy';
}

function deploymentActionKey(action: DeploymentAction): string {
  return String(
    action.id ||
      action.runId ||
      action.runDetailUrl ||
      action.logsArtifactUrl ||
      action.rawCommandLogUrl ||
      [
        action.startedAt,
        action.completedAt,
        action.requestedImage,
        action.resolvedDigest,
        action.operator,
        action.reason,
        action.status,
      ]
        .filter(Boolean)
        .join('|') ||
      'deployment-action',
  );
}

export function OperationsSettingsSection({
  workerPauseConfig,
}: {
  workerPauseConfig: WorkerPauseConfig | null;
}) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<{ level: 'ok' | 'error'; text: string } | null>(
    null,
  );
  const [deploymentNotice, setDeploymentNotice] = useState<{
    level: 'ok' | 'error';
    text: string;
  } | null>(null);
  const [pauseMode, setPauseMode] = useState('drain');
  const [pauseReason, setPauseReason] = useState('');
  const [resumeReason, setResumeReason] = useState('');
  const [targetRepository, setTargetRepository] = useState('');
  const [targetReference, setTargetReference] = useState('');
  const [updateMode, setUpdateMode] = useState('changed_services');
  const [removeOrphans, setRemoveOrphans] = useState(true);
  const [waitForServices, setWaitForServices] = useState(true);
  const [runSmokeCheck, setRunSmokeCheck] = useState(false);
  const [pauseWork, setPauseWork] = useState(false);
  const [pruneOldImages, setPruneOldImages] = useState(false);
  const [deploymentReason, setDeploymentReason] = useState('');

  const {
    data: snapshot,
    isLoading,
    isError,
    error,
  } = useQuery<WorkerSnapshot>({
    queryKey: ['workers-snapshot'],
    queryFn: async () => {
      if (!workerPauseConfig || !workerPauseConfig.get) {
        throw new Error('Worker pause controls are not configured for this deployment.');
      }
      const response = await fetch(workerPauseConfig.get, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch worker status: ${response.statusText}`);
      }
      return WorkerSnapshotSchema.parse(await response.json());
    },
    enabled: workerPauseConfig !== null,
    refetchInterval:
      workerPauseConfig !== null
        ? Math.max(1000, Number(workerPauseConfig.pollIntervalMs) || 5000)
        : false,
  });

  const actionMutation = useMutation({
    mutationFn: async (
      payload:
        | { action: 'pause'; mode: string; reason: string }
        | { action: 'resume'; reason: string; forceResume?: boolean },
    ) => {
      if (!workerPauseConfig || !workerPauseConfig.post) {
        throw new Error('Worker pause controls are not configured for this deployment.');
      }
      const response = await fetch(workerPauseConfig.post, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail =
          typeof errorPayload.detail === 'string'
            ? errorPayload.detail
            : `Server error: ${response.status}`;
        throw new Error(detail);
      }
      return WorkerSnapshotSchema.parse(await response.json());
    },
    onSuccess: (data, variables) => {
      setNotice({
        level: 'ok',
        text:
          variables.action === 'pause'
            ? 'Workers paused successfully.'
            : 'Workers resumed successfully.',
      });
      if (variables.action === 'pause') {
        setPauseReason('');
      } else {
        setResumeReason('');
      }
      queryClient.setQueryData(['workers-snapshot'], data);
    },
    onError: (mutationError: Error, variables) => {
      setNotice({
        level: 'error',
        text: `Failed to ${variables.action} workers: ${mutationError.message}`,
      });
    },
  });

  const {
    data: deploymentState,
    isLoading: isDeploymentStateLoading,
    isError: isDeploymentStateError,
    error: deploymentStateError,
  } = useQuery<DeploymentStackState>({
    queryKey: ['deployment-stack', DEPLOYMENT_STACK],
    queryFn: async () => {
      const response = await fetch(
        `/api/v1/operations/deployment/stacks/${DEPLOYMENT_STACK}`,
        { headers: { Accept: 'application/json' } },
      );
      if (!response.ok) {
        throw new Error(`Failed to fetch deployment state: ${response.statusText}`);
      }
      return DeploymentStackStateSchema.parse(await response.json());
    },
  });

  const {
    data: imageTargets,
    isLoading: areImageTargetsLoading,
    isError: areImageTargetsError,
    error: imageTargetsError,
  } = useQuery<ImageTargets>({
    queryKey: ['deployment-image-targets', DEPLOYMENT_STACK],
    queryFn: async () => {
      const response = await fetch(
        `/api/v1/operations/deployment/image-targets?stack=${encodeURIComponent(
          DEPLOYMENT_STACK,
        )}`,
        { headers: { Accept: 'application/json' } },
      );
      if (!response.ok) {
        throw new Error(`Failed to fetch deployment image targets: ${response.statusText}`);
      }
      return ImageTargetsSchema.parse(await response.json());
    },
  });

  const activeTargetRepository = useMemo(
    () =>
      imageTargets?.repositories.find(
        (repository) => repository.repository === targetRepository,
      ) ?? imageTargets?.repositories[0],
    [imageTargets, targetRepository],
  );

  const referenceOptions = useMemo(
    () =>
      uniqueStrings([
        ...(activeTargetRepository?.recentTags ?? []),
        ...(activeTargetRepository?.allowedReferences ?? []),
      ]),
    [activeTargetRepository],
  );

  const allowedModes = useMemo(
    () => {
      const explicitModes = activeTargetRepository?.allowedModes?.filter(Boolean) ?? [];
      return explicitModes.length > 0 ? explicitModes : ['changed_services'];
    },
    [activeTargetRepository],
  );

  useEffect(() => {
    const firstRepository = imageTargets?.repositories[0];
    if (!firstRepository) {
      return;
    }
    if (!targetRepository) {
      setTargetRepository(firstRepository.repository);
    }
    if (!targetReference) {
      setTargetReference(preferredReference(firstRepository));
    }
  }, [imageTargets, targetReference, targetRepository]);

  useEffect(() => {
    if (allowedModes.length > 0 && !allowedModes.includes(updateMode)) {
      setUpdateMode(allowedModes[0] ?? 'changed_services');
    }
  }, [allowedModes, updateMode]);

  const deploymentMutation = useMutation({
    mutationFn: async () => {
      const repository = targetRepository || activeTargetRepository?.repository || '';
      const reference = targetReference.trim();
      const reason = deploymentReason.trim();
      if (!repository || !reference) {
        throw new Error('Target image is required.');
      }
      if (!reason) {
        throw new Error('Deployment update reason is required.');
      }

      const targetImage = `${repository}:${reference}`;
      const confirmation = [
        'Submit deployment update?',
        `Current image: ${deploymentState?.configuredImage || 'Unavailable'}`,
        `Target image: ${targetImage}`,
        `Mode: ${modeLabel(updateMode)}`,
        `Stack: ${deploymentState?.stack || DEPLOYMENT_STACK}`,
        `Expected affected services: ${affectedServices(deploymentState)}`,
        isMutableReference(reference)
          ? 'Mutable tag warning: this tag may resolve differently over time.'
          : null,
        'Services may restart during this operation.',
      ]
        .filter(Boolean)
        .join('\n');

      if (!window.confirm(confirmation)) {
        return null;
      }

      const response = await fetch('/api/v1/operations/deployment/update', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          stack: deploymentState?.stack || DEPLOYMENT_STACK,
          image: {
            repository,
            reference,
          },
          mode: updateMode,
          removeOrphans,
          wait: waitForServices,
          runSmokeCheck,
          pauseWork,
          pruneOldImages,
          reason,
        }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail =
          typeof errorPayload.detail?.message === 'string'
            ? errorPayload.detail.message
            : typeof errorPayload.detail === 'string'
              ? errorPayload.detail
              : `Server error: ${response.status}`;
        throw new Error(detail);
      }
      return response.json() as Promise<{ deploymentUpdateRunId: string; status: string }>;
    },
    onSuccess: (result) => {
      if (!result) {
        return;
      }
      setDeploymentNotice({
        level: 'ok',
        text: `Deployment update queued: ${result.deploymentUpdateRunId}`,
      });
      setDeploymentReason('');
      queryClient.invalidateQueries({ queryKey: ['deployment-stack', DEPLOYMENT_STACK] });
    },
    onError: (mutationError: Error) => {
      setDeploymentNotice({
        level: 'error',
        text: mutationError.message,
      });
    },
  });

  const rollbackMutation = useMutation({
    mutationFn: async (action: DeploymentAction) => {
      const eligibility = action.rollbackEligibility;
      const target = eligibility?.targetImage;
      if (!eligibility?.eligible || !target) {
        throw new Error(eligibility?.reason || 'Rollback target is not available.');
      }
      const targetImage = `${target.repository}:${target.reference}`;
      const sourceActionId = String(
        eligibility.sourceActionId || action.id || action.runId || '',
      ).trim();
      if (!sourceActionId) {
        throw new Error('Rollback source action is required.');
      }
      const confirmation = [
        'Rollback deployment?',
        `Target image: ${targetImage}`,
        `Source action: ${sourceActionId}`,
        `Stack: ${deploymentState?.stack || DEPLOYMENT_STACK}`,
        'Services may restart during this operation.',
      ].join('\n');
      if (!window.confirm(confirmation)) {
        return null;
      }
      const confirmationText = `Rollback to ${targetImage} confirmed from ${sourceActionId}`;
      const response = await fetch('/api/v1/operations/deployment/update', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          stack: deploymentState?.stack || DEPLOYMENT_STACK,
          image: target,
          mode: 'changed_services',
          removeOrphans: true,
          wait: true,
          runSmokeCheck: false,
          pauseWork: false,
          pruneOldImages: false,
          reason: `Rollback after failed update ${sourceActionId}`,
          operationKind: 'rollback',
          rollbackSourceActionId: sourceActionId,
          confirmation: confirmationText,
        }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail =
          typeof errorPayload.detail?.message === 'string'
            ? errorPayload.detail.message
            : typeof errorPayload.detail === 'string'
              ? errorPayload.detail
              : `Server error: ${response.status}`;
        throw new Error(detail);
      }
      return response.json() as Promise<{ deploymentUpdateRunId: string; status: string }>;
    },
    onSuccess: (result) => {
      if (!result) {
        return;
      }
      setDeploymentNotice({
        level: 'ok',
        text: `Deployment rollback queued: ${result.deploymentUpdateRunId}`,
      });
      queryClient.invalidateQueries({ queryKey: ['deployment-stack', DEPLOYMENT_STACK] });
    },
    onError: (mutationError: Error) => {
      setDeploymentNotice({
        level: 'error',
        text: mutationError.message,
      });
    },
  });

  const handleDeploymentSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setDeploymentNotice(null);
    if (!deploymentReason.trim()) {
      setDeploymentNotice({
        level: 'error',
        text: 'Deployment update reason is required.',
      });
      return;
    }
    deploymentMutation.mutate();
  };

  const handlePause = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!pauseMode || !pauseReason) {
      setNotice({ level: 'error', text: 'Pause mode and reason are required.' });
      return;
    }
    actionMutation.mutate({ action: 'pause', mode: pauseMode, reason: pauseReason });
  };

  const handleResume = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!resumeReason) {
      setNotice({ level: 'error', text: 'Resume reason is required.' });
      return;
    }
    let forceResume = false;
    if (
      snapshot?.metrics &&
      Object.prototype.hasOwnProperty.call(snapshot.metrics, 'isDrained') &&
      !snapshot.metrics.isDrained
    ) {
      const confirmed = window.confirm('Workers are not drained yet. Resume anyway?');
      if (!confirmed) {
        return;
      }
      forceResume = true;
    }
    actionMutation.mutate({ action: 'resume', reason: resumeReason, forceResume });
  };

  const system = snapshot?.system ?? {};
  const metrics = snapshot?.metrics ?? {};
  const isPaused = Boolean(system.workersPaused);
  const stateLabel = isPaused
    ? system.mode === 'quiesce'
      ? 'Workers quiesced'
      : 'Workers draining'
    : 'Workers running';

  return (
    <div className="space-y-6">
      <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
        <div className="space-y-2">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Operations</h3>
          <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            Worker pause, drain, quiesce, and recent operational audit actions live
            here under Settings.
          </p>
        </div>
      </section>

      <section
        className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm"
        aria-label="Deployment Update"
      >
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-2">
            <h4 className="text-lg font-semibold text-slate-900 dark:text-white">
              Deployment Update
            </h4>
            <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">
              Update the configured MoonMind image for the allowlisted Compose stack.
            </p>
          </div>
          {deploymentNotice ? (
            <div
              className={`rounded-2xl border px-4 py-3 text-sm ${
                deploymentNotice.level === 'error'
                  ? 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400'
                  : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-400'
              }`}
            >
              {deploymentNotice.text}
            </div>
          ) : null}
        </div>

        {isDeploymentStateLoading || areImageTargetsLoading ? (
          <p className="mt-5 text-sm text-slate-500 dark:text-slate-400">
            Loading deployment controls...
          </p>
        ) : isDeploymentStateError || areImageTargetsError ? (
          <p className="mt-5 text-sm text-rose-700 dark:text-rose-400">
            {((deploymentStateError || imageTargetsError) as Error).message}
          </p>
        ) : (
          <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.8fr)]">
            <div className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                <div className="rounded-2xl bg-slate-50 p-4 dark:bg-slate-800/50">
                  <div className="text-sm font-medium text-slate-500 dark:text-slate-400">
                    Stack
                  </div>
                  <div className="mt-1 text-base font-semibold text-slate-900 dark:text-white">
                    {deploymentState?.stack || DEPLOYMENT_STACK}
                  </div>
                </div>
                <div className="rounded-2xl bg-slate-50 p-4 dark:bg-slate-800/50">
                  <div className="text-sm font-medium text-slate-500 dark:text-slate-400">
                    Compose project
                  </div>
                  <div className="mt-1 text-base font-semibold text-slate-900 dark:text-white">
                    {deploymentState?.projectName || '-'}
                  </div>
                </div>
                <div className="rounded-2xl bg-slate-50 p-4 dark:bg-slate-800/50">
                  <div className="text-sm font-medium text-slate-500 dark:text-slate-400">
                    Version/build
                  </div>
                  <div className="mt-1 text-base font-semibold text-slate-900 dark:text-white">
                    {deploymentState?.version || deploymentState?.build || 'Unavailable'}
                  </div>
                </div>
              </div>

              <div className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800">
                <h5 className="text-sm font-semibold text-slate-900 dark:text-white">
                  Current deployment
                </h5>
                <dl className="mt-3 space-y-3 text-sm">
                  <div>
                    <dt className="font-medium text-slate-500 dark:text-slate-400">
                      Configured image
                    </dt>
                    <dd className="break-all text-slate-900 dark:text-white">
                      {deploymentState?.configuredImage || 'Unavailable'}
                    </dd>
                  </div>
                  <div>
                    <dt className="font-medium text-slate-500 dark:text-slate-400">
                      Running image evidence
                    </dt>
                    <dd className="space-y-1 text-slate-900 dark:text-white">
                      {deploymentState?.runningImages.length ? (
                        deploymentState.runningImages.map((image) => (
                          <div key={`${image.service}-${image.image}`}>
                            {image.service}: {image.imageId || image.digest || 'Unavailable'}
                          </div>
                        ))
                      ) : (
                        <span>Unavailable</span>
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt className="font-medium text-slate-500 dark:text-slate-400">
                      Health summary
                    </dt>
                    <dd className="text-slate-900 dark:text-white">
                      {deploymentState?.services.length
                        ? deploymentState.services
                            .map(
                              (service) =>
                                `${service.name}: ${service.state}${
                                  service.health ? ` / ${service.health}` : ''
                                }`,
                            )
                            .join(', ')
                        : 'Unavailable'}
                    </dd>
                  </div>
                  <div>
                    <dt className="font-medium text-slate-500 dark:text-slate-400">
                      Last update result
                    </dt>
                    <dd className="text-slate-900 dark:text-white">
                      {deploymentState?.lastUpdateRunId || 'No previous update'}
                    </dd>
                  </div>
                </dl>
              </div>

              <div className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800">
                <h5 className="text-sm font-semibold text-slate-900 dark:text-white">
                  Recent deployment actions
                </h5>
                <div className="mt-3 space-y-3">
                  {deploymentState?.recentActions.length ? (
                    deploymentState.recentActions.map((action) => (
                      <div
                        key={deploymentActionKey(action)}
                        className="rounded-2xl bg-slate-50 p-4 text-sm dark:bg-slate-800/50"
                      >
                        <div className="font-semibold text-slate-900 dark:text-white">
                          {action.status || 'UNKNOWN'}
                        </div>
                        <div className="mt-1 break-all text-slate-600 dark:text-slate-400">
                          {action.requestedImage || 'Requested image unavailable'}
                        </div>
                        {action.resolvedDigest ? (
                          <div className="mt-1 break-all text-xs text-slate-500 dark:text-slate-400">
                            Resolved digest: {action.resolvedDigest}
                          </div>
                        ) : null}
                        <div className="mt-2 text-slate-600 dark:text-slate-400">
                          {action.operator || 'Unknown operator'} |{' '}
                          {action.reason || '(no reason)'}
                        </div>
                        <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                          {action.startedAt || '-'} {'->'} {action.completedAt || '-'}
                        </div>
                        {(action.beforeSummary || action.afterSummary) ? (
                          <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                            {action.beforeSummary || '-'} {'->'} {action.afterSummary || '-'}
                          </div>
                        ) : null}
                        <div className="mt-3 flex flex-wrap gap-3">
                          {action.runDetailUrl ? (
                            <a
                              className="text-sm font-medium text-sky-700 hover:text-sky-600 dark:text-sky-400"
                              href={action.runDetailUrl}
                            >
                              Run detail
                            </a>
                          ) : null}
                          {action.logsArtifactUrl ? (
                            <a
                              className="text-sm font-medium text-sky-700 hover:text-sky-600 dark:text-sky-400"
                              href={action.logsArtifactUrl}
                            >
                              Logs artifact
                            </a>
                          ) : null}
                          {action.rawCommandLogPermitted && action.rawCommandLogUrl ? (
                            <a
                              className="text-sm font-medium text-sky-700 hover:text-sky-600 dark:text-sky-400"
                              href={action.rawCommandLogUrl}
                            >
                              Raw command log
                            </a>
                          ) : null}
                          {action.rollbackEligibility?.eligible &&
                          action.rollbackEligibility.targetImage ? (
                            <button
                              type="button"
                              className="text-sm font-medium text-sky-700 hover:text-sky-600 dark:text-sky-400"
                              onClick={() => rollbackMutation.mutate(action)}
                            >
                              Roll back to {action.rollbackEligibility.targetImage.reference}
                            </button>
                          ) : action.rollbackEligibility &&
                            !action.rollbackEligibility.eligible &&
                            action.rollbackEligibility.reason ? (
                            <span className="text-sm text-slate-500 dark:text-slate-400">
                              {action.rollbackEligibility.reason}
                            </span>
                          ) : null}
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-slate-500 dark:text-slate-400">
                      No recent deployment update actions.
                    </p>
                  )}
                </div>
              </div>
            </div>

            <form
              className="space-y-4 rounded-2xl border border-slate-200 p-5 dark:border-slate-800"
              onSubmit={handleDeploymentSubmit}
              noValidate
            >
              <h5 className="text-sm font-semibold text-slate-900 dark:text-white">
                Update target
              </h5>
              <label className="block space-y-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Image repository</span>
                <select
                  className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                  value={targetRepository}
                  onChange={(event) => {
                    const nextRepository = event.target.value;
                    setTargetRepository(nextRepository);
                    const repository = imageTargets?.repositories.find(
                      (candidate) => candidate.repository === nextRepository,
                    );
                    setTargetReference(preferredReference(repository));
                  }}
                >
                  {imageTargets?.repositories.map((repository) => (
                    <option key={repository.repository} value={repository.repository}>
                      {repository.repository}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block space-y-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Target reference</span>
                <select
                  className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                  value={targetReference}
                  onChange={(event) => setTargetReference(event.target.value)}
                >
                  {referenceOptions.map((reference) => (
                    <option key={reference} value={reference}>
                      {reference}
                    </option>
                  ))}
                </select>
              </label>
              {isMutableReference(targetReference) ? (
                <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-300">
                  {targetReference} may resolve differently over time. Digest-pinned or
                  release-tagged updates are preferred.
                </div>
              ) : null}
              <label className="block space-y-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Update mode</span>
                <select
                  className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                  value={updateMode}
                  onChange={(event) => setUpdateMode(event.target.value)}
                >
                  {allowedModes.map((mode) => (
                    <option key={mode} value={mode}>
                      {modeLabel(mode)}
                    </option>
                  ))}
                </select>
              </label>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                {modeDescription(updateMode)}
              </p>
              {allowedModes.includes('force_recreate') ? (
                <p className="text-sm text-amber-700 dark:text-amber-300">
                  Force recreate all services will recreate every service in the
                  allowlisted stack.
                </p>
              ) : null}

              <fieldset className="space-y-2 text-sm text-slate-700 dark:text-slate-300">
                <legend className="font-medium">Options</legend>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={removeOrphans}
                    onChange={(event) => setRemoveOrphans(event.target.checked)}
                  />
                  Remove orphan containers
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={waitForServices}
                    onChange={(event) => setWaitForServices(event.target.checked)}
                  />
                  Wait for services
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={runSmokeCheck}
                    onChange={(event) => setRunSmokeCheck(event.target.checked)}
                  />
                  Run smoke check
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={pauseWork}
                    onChange={(event) => setPauseWork(event.target.checked)}
                  />
                  Pause or drain new task work
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={pruneOldImages}
                    onChange={(event) => setPruneOldImages(event.target.checked)}
                  />
                  Prune old images after success
                </label>
              </fieldset>

              <label className="block space-y-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Reason</span>
                <input
                  className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                  type="text"
                  maxLength={200}
                  value={deploymentReason}
                  onChange={(event) => setDeploymentReason(event.target.value)}
                  required
                />
              </label>

              <button
                type="submit"
                disabled={deploymentMutation.isPending}
                className="inline-flex items-center justify-center rounded-full bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
              >
                Submit deployment update
              </button>
            </form>
          </div>
        )}
      </section>

      {!workerPauseConfig ? (
        <section className="rounded-3xl border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900 shadow-sm dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-400">
          Worker pause controls are not configured for this deployment.
        </section>
      ) : null}

      {workerPauseConfig ? (
      <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
        {notice ? (
          <div
            className={`mb-4 rounded-2xl border px-4 py-3 text-sm shadow-sm ${
              notice.level === 'error'
                ? 'border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-400'
                : 'border-emerald-200 dark:border-emerald-900/50 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400'
            }`}
          >
            {notice.text}
          </div>
        ) : null}

        {isLoading ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">Loading worker status...</p>
        ) : isError ? (
          <p className="text-sm text-rose-700 dark:text-rose-400">{(error as Error).message}</p>
        ) : (
          <div className="space-y-6">
            <div className="space-y-2">
              <h4 className="text-lg font-semibold text-slate-900 dark:text-white">{stateLabel}</h4>
              <p className="text-sm text-slate-600 dark:text-slate-400">{system.reason || 'Normal operation'}</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Mode: {system.mode || (isPaused ? 'paused' : 'running')} | Version:{' '}
                {system.version || '-'} | Updated: {system.updatedAt || '-'}
              </p>
            </div>

            <div className="grid gap-4 md:grid-cols-4">
              <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 p-4 text-center">
                <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Queued</div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">{metrics.queued || 0}</div>
              </div>
              <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 p-4 text-center">
                <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Running</div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">{metrics.running || 0}</div>
              </div>
              <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 p-4 text-center">
                <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Stale</div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">
                  {metrics.staleRunning || 0}
                </div>
              </div>
              <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 p-4 text-center">
                <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Drained</div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">
                  {metrics.isDrained ? 'Yes' : 'No'}
                </div>
              </div>
            </div>

            <div className="grid gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
              <section className="rounded-3xl border border-slate-200 dark:border-slate-800 p-5">
                <h4 className="text-base font-semibold text-slate-900 dark:text-white">Worker controls</h4>
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                  Drain lets running jobs finish. Quiesce stops new claims immediately.
                </p>

                <form className="mt-5 space-y-4" onSubmit={handlePause}>
                  <fieldset className="space-y-3" disabled={actionMutation.isPending}>
                    <legend className="text-sm font-medium text-slate-700 dark:text-300">
                      Pause workers
                    </legend>
                    <label className="block space-y-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                      <span>Mode</span>
                      <select
                        className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                        value={pauseMode}
                        onChange={(event) => setPauseMode(event.target.value)}
                      >
                        <option value="drain">Drain</option>
                        <option value="quiesce">Quiesce</option>
                      </select>
                    </label>
                    <label className="block space-y-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                      <span>Reason</span>
                      <input
                        className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                        type="text"
                        maxLength={160}
                        value={pauseReason}
                        onChange={(event) => setPauseReason(event.target.value)}
                        required
                      />
                    </label>
                    <button
                      type="submit"
                      className="inline-flex items-center justify-center rounded-full bg-slate-900 dark:bg-slate-100 px-5 py-2.5 text-sm font-semibold text-white dark:text-slate-900 transition hover:bg-slate-800 dark:hover:bg-slate-200"
                    >
                      Pause workers
                    </button>
                  </fieldset>
                </form>

                <div className="my-6 border-t border-slate-200 dark:border-slate-800" />

                <form className="space-y-4" onSubmit={handleResume}>
                  <fieldset className="space-y-3" disabled={actionMutation.isPending}>
                    <legend className="text-sm font-medium text-slate-700 dark:text-slate-300">
                      Resume workers
                    </legend>
                    <label className="block space-y-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                      <span>Reason</span>
                      <input
                        className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                        type="text"
                        maxLength={160}
                        value={resumeReason}
                        onChange={(event) => setResumeReason(event.target.value)}
                        required
                      />
                    </label>
                    <button
                      type="submit"
                      className="inline-flex items-center justify-center rounded-full bg-emerald-600 dark:bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-white dark:text-white transition hover:bg-emerald-500 dark:hover:bg-emerald-400"
                    >
                      Resume workers
                    </button>
                  </fieldset>
                </form>
              </section>

              <section className="rounded-3xl border border-slate-200 dark:border-slate-800 p-5">
                <h4 className="text-base font-semibold text-slate-900 dark:text-white">Recent actions</h4>
                <div className="mt-4 space-y-3">
                  {snapshot?.audit?.latest && snapshot.audit.latest.length > 0 ? (
                    snapshot.audit.latest.map((event, index) => (
                      <div
                        key={`${event.createdAt || 'event'}-${index}`}
                        className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 p-4"
                      >
                        <div className="text-sm font-semibold text-slate-900 dark:text-white">
                          {(event.action || '-').toUpperCase()}
                          {event.mode ? ` | ${event.mode.toUpperCase()}` : ''}
                        </div>
                        <div className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                          {event.reason || '(no reason)'}
                        </div>
                        <time
                          dateTime={event.createdAt}
                          className="mt-2 block text-xs text-slate-500 dark:text-slate-400"
                        >
                          {event.createdAt || '-'}
                        </time>
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-slate-500 dark:text-slate-400">No recent pause or resume actions.</p>
                  )}
                </div>
              </section>
            </div>
          </div>
        )}
      </section>
      ) : null}
    </div>
  );
}
