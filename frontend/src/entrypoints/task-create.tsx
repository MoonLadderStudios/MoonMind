import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { mountPage } from '../boot/mountPage';
import type { BootPayload } from '../boot/parseBootPayload';

const INLINE_INSTRUCTIONS_LIMIT = 8_000;

interface DashboardConfig {
  sources?: {
    temporal?: {
      create?: string;
      artifactCreate?: string;
    };
  };
  system?: {
    defaultRepository?: string;
    defaultTaskRuntime?: string;
    defaultTaskModel?: string;
    defaultTaskEffort?: string;
    defaultPublishMode?: string;
    defaultTaskModelByRuntime?: Record<string, string>;
    defaultTaskEffortByRuntime?: Record<string, string>;
    supportedTaskRuntimes?: string[];
    providerProfiles?: {
      list?: string;
    };
  };
}

interface ProviderProfile {
  profile_id: string;
  account_label?: string | null;
}

interface SkillsResponse {
  items?: {
    worker?: string[];
  };
}

interface ExecutionCreateResponse {
  workflowId?: string;
  runId?: string;
  temporalRunId?: string;
  namespace?: string;
  redirectPath?: string;
}

function readDashboardConfig(payload: BootPayload): DashboardConfig {
  const raw = payload.initialData as { dashboardConfig?: DashboardConfig } | undefined;
  return raw?.dashboardConfig ?? {};
}

function navigateTo(path: string): void {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

async function createInputArtifact(
  createEndpoint: string,
  instructions: string,
): Promise<{ artifactId: string }> {
  const createResponse = await fetch(createEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify({
      content_type: 'text/plain; charset=utf-8',
      size_bytes: new TextEncoder().encode(instructions).length,
      metadata: {
        purpose: 'input.instructions',
      },
    }),
  });
  if (!createResponse.ok) {
    throw new Error('Failed to create input artifact.');
  }
  const created = (await createResponse.json()) as {
    artifact_ref?: {
      artifact_id?: string;
    };
    upload?: {
      upload_url?: string;
    };
  };
  const artifactId = String(created.artifact_ref?.artifact_id || '').trim();
  const uploadUrl = String(created.upload?.upload_url || '').trim();
  if (!artifactId || !uploadUrl) {
    throw new Error('Artifact upload details were incomplete.');
  }

  const uploadResponse = await fetch(uploadUrl, {
    method: 'PUT',
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
    },
    body: instructions,
  });
  if (!uploadResponse.ok) {
    throw new Error('Failed to upload input artifact content.');
  }

  return { artifactId };
}

async function linkInputArtifact(
  artifactId: string,
  execution: ExecutionCreateResponse,
): Promise<void> {
  const workflowId = String(execution.workflowId || '').trim();
  const runId = String(execution.runId || execution.temporalRunId || '').trim();
  const namespace = String(execution.namespace || '').trim();
  if (!artifactId || !workflowId || !runId || !namespace) {
    return;
  }
  const response = await fetch(`/api/artifacts/${encodeURIComponent(artifactId)}/links`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify({
      namespace,
      workflow_id: workflowId,
      run_id: runId,
      link_type: 'input.instructions',
      label: 'Mission Control task instructions',
    }),
  });
  if (!response.ok) {
    throw new Error('Failed to link input artifact to execution.');
  }
}

export function TaskCreatePage({ payload }: { payload: BootPayload }) {
  const dashboardConfig = readDashboardConfig(payload);
  const temporalCreateEndpoint = String(dashboardConfig.sources?.temporal?.create || '/api/executions');
  const artifactCreateEndpoint = String(
    dashboardConfig.sources?.temporal?.artifactCreate || '/api/artifacts',
  );
  const providerProfilesEndpoint = String(
    dashboardConfig.system?.providerProfiles?.list || '/api/v1/provider-profiles',
  );
  const defaultRuntime = String(dashboardConfig.system?.defaultTaskRuntime || 'codex_cli');
  const defaultRepository = String(dashboardConfig.system?.defaultRepository || '');
  const defaultPublishMode = String(dashboardConfig.system?.defaultPublishMode || 'pr');
  const defaultTaskModelByRuntime = dashboardConfig.system?.defaultTaskModelByRuntime || {};
  const defaultTaskEffortByRuntime = dashboardConfig.system?.defaultTaskEffortByRuntime || {};
  const supportedTaskRuntimes = dashboardConfig.system?.supportedTaskRuntimes || [
    'codex_cli',
    'gemini_cli',
    'claude_code',
  ];

  const [instructions, setInstructions] = useState('');
  const [repository, setRepository] = useState(defaultRepository);
  const [skill, setSkill] = useState('');
  const [runtime, setRuntime] = useState(defaultRuntime);
  const [model, setModel] = useState(
    String(defaultTaskModelByRuntime[defaultRuntime] || dashboardConfig.system?.defaultTaskModel || ''),
  );
  const [effort, setEffort] = useState(
    String(defaultTaskEffortByRuntime[defaultRuntime] || dashboardConfig.system?.defaultTaskEffort || ''),
  );
  const [publishMode, setPublishMode] = useState(defaultPublishMode);
  const [providerProfile, setProviderProfile] = useState('');
  const [startingBranch, setStartingBranch] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    setModel(String(defaultTaskModelByRuntime[runtime] || ''));
    setEffort(String(defaultTaskEffortByRuntime[runtime] || ''));
    setProviderProfile('');
  }, [defaultTaskEffortByRuntime, defaultTaskModelByRuntime, runtime]);

  const skillsQuery = useQuery({
    queryKey: ['task-create', 'skills'],
    queryFn: async (): Promise<string[]> => {
      const response = await fetch('/api/tasks/skills', {
        headers: {
          Accept: 'application/json',
        },
      });
      if (!response.ok) {
        throw new Error('Failed to load skills.');
      }
      const data = (await response.json()) as SkillsResponse;
      return data.items?.worker || [];
    },
  });

  const providerProfilesQuery = useQuery({
    queryKey: ['task-create', 'provider-profiles', runtime],
    queryFn: async (): Promise<ProviderProfile[]> => {
      const separator = providerProfilesEndpoint.includes('?') ? '&' : '?';
      const response = await fetch(
        `${providerProfilesEndpoint}${separator}runtime_id=${encodeURIComponent(runtime)}`,
        {
          headers: {
            Accept: 'application/json',
          },
        },
      );
      if (!response.ok) {
        throw new Error('Failed to load provider profiles.');
      }
      return (await response.json()) as ProviderProfile[];
    },
    enabled: Boolean(runtime),
  });

  const providerOptions = useMemo(
    () =>
      (providerProfilesQuery.data || []).map((profile) => ({
        id: profile.profile_id,
        label: profile.account_label || profile.profile_id,
      })),
    [providerProfilesQuery.data],
  );

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    if (!instructions.trim()) {
      setMessage('Instructions are required.');
      return;
    }
    if (!repository.trim()) {
      setMessage('Repository is required.');
      return;
    }

    setIsSubmitting(true);
    setMessage(null);

    try {
      let inputArtifactRef: string | null = null;
      let submittedInstructions = instructions.trim();
      if (submittedInstructions.length > INLINE_INSTRUCTIONS_LIMIT) {
        const artifact = await createInputArtifact(artifactCreateEndpoint, submittedInstructions);
        inputArtifactRef = artifact.artifactId;
        submittedInstructions = '';
      }

      const requestBody = {
        type: 'task',
        payload: {
          repository: repository.trim(),
          ...(inputArtifactRef ? { inputArtifactRef } : {}),
          task: {
            instructions: submittedInstructions,
            ...(skill.trim() ? { skill: skill.trim(), skills: [skill.trim()] } : {}),
            runtime: {
              mode: runtime,
              model: model.trim(),
              effort: effort.trim(),
              ...(providerProfile ? { profileId: providerProfile } : {}),
            },
            publish: {
              mode: publishMode,
            },
            ...(startingBranch.trim()
              ? {
                  git: {
                    startingBranch: startingBranch.trim(),
                  },
                }
              : {}),
          },
        },
      };

      const response = await fetch(temporalCreateEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(requestBody),
      });
      if (!response.ok) {
        throw new Error('Failed to create queue task');
      }
      const created = (await response.json()) as ExecutionCreateResponse;
      if (inputArtifactRef) {
        await linkInputArtifact(inputArtifactRef, created);
      }
      const redirectPath =
        String(created.redirectPath || '').trim() ||
        (created.workflowId
          ? `/tasks/${encodeURIComponent(created.workflowId)}?source=temporal`
          : '');
      if (!redirectPath) {
        throw new Error('Task was created but no redirect path was returned.');
      }
      navigateTo(redirectPath);
    } catch (error) {
      const text = error instanceof Error ? error.message : 'Failed to create queue task';
      setMessage(text);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="space-y-6">
        <header className="rounded-[2rem] border border-mm-border/80 bg-transparent px-6 py-6 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
            Temporal Submission
          </p>
          <h2 className="mt-2 text-3xl font-semibold tracking-tight text-slate-950 dark:text-white">
            Create Task
          </h2>
          <p className="mt-2 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            Submit a Temporal-backed Mission Control task without the legacy dashboard shell.
          </p>
        </header>

        <form id="queue-submit-form" className="queue-submit-form" onSubmit={handleSubmit}>
          <label>
            Instructions
            <textarea
              name="instructions"
              data-step-field="instructions"
              data-step-index="0"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              className="queue-step-instructions"
            />
          </label>

          <div className="grid gap-4 md:grid-cols-2">
            <label>
              Repository
              <input
                name="repository"
                value={repository}
                onChange={(event) => setRepository(event.target.value)}
              />
            </label>

            <label>
              Skill
              <input
                name="skill"
                list="task-create-skill-options"
                value={skill}
                onChange={(event) => setSkill(event.target.value)}
              />
              <datalist id="task-create-skill-options">
                {(skillsQuery.data || []).map((item) => (
                  <option key={item} value={item} />
                ))}
              </datalist>
            </label>

            <label>
              Runtime
              <select
                name="runtime"
                value={runtime}
                onChange={(event) => setRuntime(event.target.value)}
              >
                {supportedTaskRuntimes.map((runtimeOption) => (
                  <option key={runtimeOption} value={runtimeOption}>
                    {runtimeOption}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Model
              <input
                name="model"
                value={model}
                onChange={(event) => setModel(event.target.value)}
              />
            </label>

            <label>
              Effort
              <select
                name="effort"
                value={effort}
                onChange={(event) => setEffort(event.target.value)}
              >
                {['low', 'medium', 'high', 'xhigh'].map((effortOption) => (
                  <option key={effortOption} value={effortOption}>
                    {effortOption}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Publish Mode
              <select
                name="publishMode"
                value={publishMode}
                onChange={(event) => setPublishMode(event.target.value)}
              >
                {['pr', 'branch', 'none'].map((mode) => (
                  <option key={mode} value={mode}>
                    {mode}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Starting Branch
              <input
                name="startingBranch"
                value={startingBranch}
                onChange={(event) => setStartingBranch(event.target.value)}
              />
            </label>

            <div id="queue-provider-profile-wrap" hidden={providerOptions.length === 0}>
              <label>
                Provider Profile
                <select
                  name="providerProfile"
                  value={providerProfile}
                  onChange={(event) => setProviderProfile(event.target.value)}
                >
                  <option value="">Default (system chooses)</option>
                  {providerOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          <div className="actions">
            <button
              type="submit"
              className="queue-submit-primary"
              disabled={isSubmitting}
            >
              {isSubmitting ? 'Submitting...' : 'Create'}
            </button>
          </div>

          <p
            id="queue-submit-message"
            className={`queue-submit-message${message ? ' notice error' : ''}`}
          >
            {message || ''}
          </p>
        </form>
      </div>
    </div>
  );
}

mountPage(TaskCreatePage);
