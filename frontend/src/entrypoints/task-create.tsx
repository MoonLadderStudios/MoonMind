import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { mountPage } from '../boot/mountPage';
import type { BootPayload } from '../boot/parseBootPayload';
import { navigateTo } from '../lib/navigation';

const INLINE_INSTRUCTIONS_LIMIT = 8_000;
const SKILL_OPTIONS_DATALIST_ID = 'task-create-skill-options';

type TemplateScope = 'global' | 'personal';
type PresetApplyMode = 'append' | 'replace';

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
    defaultProposeTasks?: boolean;
    defaultTaskModelByRuntime?: Record<string, string>;
    defaultTaskEffortByRuntime?: Record<string, string>;
    supportedTaskRuntimes?: string[];
    providerProfiles?: {
      list?: string;
    };
    taskTemplateCatalog?: {
      enabled?: boolean;
      list?: string;
      detail?: string;
      expand?: string;
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

interface TaskTemplateInputDefinition {
  name: string;
  label: string;
  type: 'text' | 'textarea' | 'markdown' | 'enum' | 'boolean' | 'user' | 'team' | 'repo_path';
  required?: boolean;
  default?: unknown;
  options?: string[];
}

interface TaskTemplateSummary {
  slug: string;
  scope: TemplateScope;
  scopeRef?: string | null;
  title: string;
  description: string;
  latestVersion: string;
  version: string;
}

interface TaskTemplateDetail extends TaskTemplateSummary {
  inputs?: TaskTemplateInputDefinition[];
}

interface TaskTemplateListResponse {
  items?: TaskTemplateSummary[];
}

interface TaskTemplateStepSkill {
  id?: string;
  name?: string;
  args?: Record<string, unknown>;
  inputs?: Record<string, unknown>;
  requiredCapabilities?: string[];
}

interface ExpandedStepPayload {
  id?: string;
  title?: string;
  instructions?: string;
  skill?: TaskTemplateStepSkill;
  tool?: TaskTemplateStepSkill;
  [key: string]: unknown;
}

interface TaskTemplateExpandResponse {
  steps?: ExpandedStepPayload[];
  appliedTemplate?: {
    slug?: string;
    version?: string;
  };
  warnings?: string[];
}

interface TemplateOption extends TaskTemplateSummary {
  key: string;
}

interface DraftStep {
  localId: string;
  id: string;
  title: string;
  instructions: string;
  skillId: string;
  skillArgs: Record<string, unknown>;
  requiredCapabilities: string[];
  extras: Record<string, unknown>;
  originLabel: string | null;
}

function readDashboardConfig(payload: BootPayload): DashboardConfig {
  const raw = payload.initialData as { dashboardConfig?: DashboardConfig } | undefined;
  return raw?.dashboardConfig ?? {};
}

function templateKey(scope: TemplateScope, slug: string): string {
  return `${scope}:${slug}`;
}

function interpolatePath(template: string, replacements: Record<string, string>): string {
  return Object.entries(replacements).reduce(
    (value, [key, replacement]) => value.replaceAll(`{${key}}`, encodeURIComponent(replacement)),
    template,
  );
}

function withQueryParams(
  baseUrl: string,
  params: Record<string, string | null | undefined>,
): string {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      searchParams.set(key, String(value));
    }
  });
  const serialized = searchParams.toString();
  if (!serialized) {
    return baseUrl;
  }
  return `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}${serialized}`;
}

function defaultValueForTemplateInput(definition: TaskTemplateInputDefinition): unknown {
  if (definition.default !== undefined && definition.default !== null) {
    return definition.default;
  }
  if (definition.type === 'boolean') {
    return false;
  }
  return '';
}

function createDraftStep(index: number): DraftStep {
  const id = `draft-step-${index}`;
  return {
    localId: id,
    id,
    title: '',
    instructions: '',
    skillId: '',
    skillArgs: {},
    requiredCapabilities: [],
    extras: {},
    originLabel: null,
  };
}

function isDraftStepEmpty(step: DraftStep): boolean {
  return !step.title.trim() && !step.instructions.trim() && !step.skillId.trim();
}

function serializeDraftStep(step: DraftStep): Record<string, unknown> {
  const serialized: Record<string, unknown> = { ...step.extras };
  if (step.id.trim()) {
    serialized.id = step.id.trim();
  }
  if (step.title.trim()) {
    serialized.title = step.title.trim();
  }
  if (step.instructions.trim()) {
    serialized.instructions = step.instructions.trim();
  }
  if (step.skillId.trim()) {
    serialized.skill = {
      id: step.skillId.trim(),
      ...(Object.keys(step.skillArgs).length > 0 ? { args: step.skillArgs } : {}),
      ...(step.requiredCapabilities.length > 0
        ? { requiredCapabilities: step.requiredCapabilities }
        : {}),
    };
  }
  return serialized;
}

function buildPresetOriginLabel(template: TaskTemplateSummary, version: string): string {
  return `${template.title} (${template.scope}, ${version})`;
}

function mapExpandedStepsToDraftSteps(
  expandedSteps: ExpandedStepPayload[],
  template: TaskTemplateSummary,
  version: string,
  startIndex: number,
): DraftStep[] {
  return expandedSteps.map((step, offset) => {
    const fallbackId = `preset-step-${startIndex + offset}`;
    const rawSkill = step.skill || step.tool || {};
    const skillId = String(rawSkill.id || rawSkill.name || '').trim();
    const rawArgs =
      rawSkill.args && typeof rawSkill.args === 'object'
        ? rawSkill.args
        : rawSkill.inputs && typeof rawSkill.inputs === 'object'
          ? rawSkill.inputs
          : {};
    const extras = Object.fromEntries(
      Object.entries(step).filter(([key]) => !['id', 'title', 'instructions', 'skill', 'tool'].includes(key)),
    );
    const id = String(step.id || '').trim() || fallbackId;
    return {
      localId: `${id}-${startIndex + offset}`,
      id,
      title: String(step.title || '').trim(),
      instructions: String(step.instructions || '').trim(),
      skillId,
      skillArgs: { ...rawArgs },
      requiredCapabilities: Array.isArray(rawSkill.requiredCapabilities)
        ? rawSkill.requiredCapabilities
            .map((value) => String(value || '').trim())
            .filter(Boolean)
        : [],
      extras,
      originLabel: buildPresetOriginLabel(template, version),
    };
  });
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
  const taskTemplateCatalog = dashboardConfig.system?.taskTemplateCatalog;
  const taskTemplateCatalogEnabled = Boolean(taskTemplateCatalog?.enabled);
  const taskTemplateListEndpoint = String(taskTemplateCatalog?.list || '/api/task-step-templates');
  const taskTemplateDetailEndpoint = String(
    taskTemplateCatalog?.detail || '/api/task-step-templates/{slug}',
  );
  const taskTemplateExpandEndpoint = String(
    taskTemplateCatalog?.expand || '/api/task-step-templates/{slug}:expand',
  );
  const defaultRuntime = String(dashboardConfig.system?.defaultTaskRuntime || 'codex_cli');
  const defaultRepository = String(dashboardConfig.system?.defaultRepository || '');
  const defaultPublishMode = String(dashboardConfig.system?.defaultPublishMode || 'pr');
  const defaultProposeTasks = Boolean(dashboardConfig.system?.defaultProposeTasks);
  const defaultTaskModelByRuntime = useMemo(
    () => dashboardConfig.system?.defaultTaskModelByRuntime || {},
    [dashboardConfig.system?.defaultTaskModelByRuntime],
  );
  const defaultTaskEffortByRuntime = useMemo(
    () => dashboardConfig.system?.defaultTaskEffortByRuntime || {},
    [dashboardConfig.system?.defaultTaskEffortByRuntime],
  );
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
  const [proposeTasks, setProposeTasks] = useState(defaultProposeTasks);
  const [providerProfile, setProviderProfile] = useState('');
  const [startingBranch, setStartingBranch] = useState('');
  const [steps, setSteps] = useState<DraftStep[]>([createDraftStep(1)]);
  const [nextStepNumber, setNextStepNumber] = useState(2);
  const [selectedPresetKey, setSelectedPresetKey] = useState('');
  const [presetApplyMode, setPresetApplyMode] = useState<PresetApplyMode>('append');
  const [presetInputs, setPresetInputs] = useState<Record<string, unknown>>({});
  const [presetMessage, setPresetMessage] = useState<string | null>(null);
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [isApplyingPreset, setIsApplyingPreset] = useState(false);
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

  const templateOptionsQuery = useQuery({
    queryKey: ['task-create', 'task-template-catalog', taskTemplateListEndpoint],
    enabled: taskTemplateCatalogEnabled,
    queryFn: async (): Promise<TemplateOption[]> => {
      const scopes: TemplateScope[] = ['personal', 'global'];
      const results = await Promise.all(
        scopes.map(async (scope) => {
          const response = await fetch(
            withQueryParams(taskTemplateListEndpoint, { scope }),
            {
              headers: {
                Accept: 'application/json',
              },
            },
          );
          if (!response.ok) {
            throw new Error('Failed to load presets.');
          }
          const data = (await response.json()) as TaskTemplateListResponse;
          return (data.items || []).map((item) => ({
            ...item,
            key: templateKey(item.scope, item.slug),
          }));
        }),
      );
      return results.flat().sort((left, right) => left.title.localeCompare(right.title));
    },
  });

  const selectedPreset = useMemo(
    () => templateOptionsQuery.data?.find((item) => item.key === selectedPresetKey) || null,
    [selectedPresetKey, templateOptionsQuery.data],
  );

  const selectedPresetDetailQuery = useQuery({
    queryKey: ['task-create', 'task-template-detail', selectedPresetKey],
    enabled: Boolean(taskTemplateCatalogEnabled && selectedPreset),
    queryFn: async (): Promise<TaskTemplateDetail> => {
      if (!selectedPreset) {
        throw new Error('Preset was not selected.');
      }
      const detailUrl = withQueryParams(
        interpolatePath(taskTemplateDetailEndpoint, { slug: selectedPreset.slug }),
        {
          scope: selectedPreset.scope,
          scopeRef: selectedPreset.scopeRef || undefined,
        },
      );
      const response = await fetch(detailUrl, {
        headers: {
          Accept: 'application/json',
        },
      });
      if (!response.ok) {
        throw new Error('Failed to load preset details.');
      }
      return (await response.json()) as TaskTemplateDetail;
    },
  });

  useEffect(() => {
    if (!selectedPresetDetailQuery.data) {
      setPresetInputs({});
      return;
    }
    const nextInputs = Object.fromEntries(
      (selectedPresetDetailQuery.data.inputs || []).map((definition) => [
        definition.name,
        defaultValueForTemplateInput(definition),
      ]),
    );
    setPresetInputs(nextInputs);
    setPresetMessage(null);
  }, [selectedPresetDetailQuery.data, selectedPresetKey]);

  const providerOptions = useMemo(
    () =>
      (providerProfilesQuery.data || []).map((profile) => ({
        id: profile.profile_id,
        label: profile.account_label || profile.profile_id,
      })),
    [providerProfilesQuery.data],
  );

  const addStep = () => {
    setSteps((current) => [...current, createDraftStep(nextStepNumber)]);
    setNextStepNumber((current) => current + 1);
  };

  const updateStep = (localId: string, updates: Partial<DraftStep>) => {
    setSteps((current) =>
      current.map((step) => (step.localId === localId ? { ...step, ...updates } : step)),
    );
  };

  const removeStep = (localId: string) => {
    setSteps((current) => current.filter((step) => step.localId !== localId));
  };

  const handleApplyPreset = async () => {
    if (!selectedPreset || !selectedPresetDetailQuery.data) {
      setPresetMessage('Select a preset before applying it.');
      return;
    }

    setIsApplyingPreset(true);
    setPresetMessage(null);

    try {
      const response = await fetch(
        withQueryParams(
          interpolatePath(taskTemplateExpandEndpoint, { slug: selectedPreset.slug }),
          {
            scope: selectedPreset.scope,
            scopeRef: selectedPreset.scopeRef || undefined,
          },
        ),
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
          },
          body: JSON.stringify({
            version: selectedPresetDetailQuery.data.latestVersion,
            inputs: presetInputs,
            context: {
              repository: repository.trim(),
              runtime,
              publishMode,
              startingBranch: startingBranch.trim(),
            },
            options: {
              enforceStepLimit: true,
            },
          }),
        },
      );
      if (!response.ok) {
        throw new Error('Failed to apply preset.');
      }
      const expanded = (await response.json()) as TaskTemplateExpandResponse;
      const expandedSteps = mapExpandedStepsToDraftSteps(
        expanded.steps || [],
        selectedPreset,
        String(expanded.appliedTemplate?.version || selectedPresetDetailQuery.data.latestVersion),
        nextStepNumber,
      );
      setNextStepNumber((current) => current + Math.max(expandedSteps.length, 1));
      setSteps((current) => {
        const meaningfulExistingSteps = current.filter((step) => !isDraftStepEmpty(step));
        return presetApplyMode === 'replace'
          ? expandedSteps
          : [...meaningfulExistingSteps, ...expandedSteps];
      });
      const warnings = (expanded.warnings || []).filter(Boolean);
      setPresetMessage(
        warnings.length > 0
          ? `Preset applied with warnings: ${warnings.join(' ')}`
          : `Applied preset "${selectedPreset.title}".`,
      );
    } catch (error) {
      const text = error instanceof Error ? error.message : 'Failed to apply preset.';
      setPresetMessage(text);
    } finally {
      setIsApplyingPreset(false);
    }
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }

    const normalizedSteps = steps
      .filter((step) => !isDraftStepEmpty(step))
      .map((step) => serializeDraftStep(step));
    const selectedSkill = skill.trim();
    const trimmedInstructions = instructions.trim();

    if (!trimmedInstructions && !selectedSkill && normalizedSteps.length === 0) {
      setSubmitMessage('Add task instructions, select a skill, or define at least one step.');
      return;
    }
    if (!repository.trim()) {
      setSubmitMessage('Repository is required.');
      return;
    }

    setIsSubmitting(true);
    setSubmitMessage(null);

    try {
      // When there is exactly one non-empty step, lift its fields into task-level
      // inputs so the Temporal planner (which uses task-level fields for single-node
      // runs) picks them up correctly. The multi-step path (len > 1) remains unchanged.
      let submittedSteps: Record<string, unknown>[] | null = null;
      let stepLiftedInstructions = '';
      let stepLiftedSkill: { type: string; name: string; version: string } | null = null;
      if (normalizedSteps.length === 1) {
        // normalizedSteps[0] is guaranteed to exist here (length === 1).
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const onlyStep = normalizedSteps[0]!;
        if (!trimmedInstructions) {
          stepLiftedInstructions = String(onlyStep.instructions || '');
        }
        if (!selectedSkill && onlyStep.skill && typeof onlyStep.skill === 'object') {
          const s = onlyStep.skill as Record<string, unknown>;
          const skillName = String(s.id || s.name || '').trim();
          if (skillName) {
            stepLiftedSkill = { type: 'skill', name: skillName, version: '1.0' };
          }
        }
        // Do not include steps in the payload — use task-level fields instead.
      } else if (normalizedSteps.length > 1) {
        submittedSteps = normalizedSteps;
      }

      let inputArtifactRef: string | null = null;
      let submittedInstructions = trimmedInstructions || stepLiftedInstructions;
      if (submittedInstructions.length > INLINE_INSTRUCTIONS_LIMIT) {
        const artifact = await createInputArtifact(artifactCreateEndpoint, submittedInstructions);
        inputArtifactRef = artifact.artifactId;
        submittedInstructions = '';
      }

      const effectiveSkill = selectedSkill
        ? { type: 'skill', name: selectedSkill, version: '1.0' }
        : stepLiftedSkill;

      const requestBody = {
        type: 'task',
        payload: {
          repository: repository.trim(),
          ...(inputArtifactRef ? { inputArtifactRef } : {}),
          task: {
            ...(submittedInstructions ? { instructions: submittedInstructions } : {}),
            ...(effectiveSkill
              ? {
                  tool: effectiveSkill,
                }
              : {}),
            ...(submittedSteps !== null ? { steps: submittedSteps } : {}),
            runtime: {
              mode: runtime,
              ...(model.trim() ? { model: model.trim() } : {}),
              ...(effort.trim() ? { effort: effort.trim() } : {}),
              ...(providerProfile ? { profileId: providerProfile } : {}),
            },
            publish: {
              mode: publishMode,
            },
            proposeTasks,
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
      setSubmitMessage(text);
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
            Restore the full task builder: define an objective, apply presets, add steps, and
            submit the result through the Temporal-backed execution API.
          </p>
        </header>

        <form id="queue-submit-form" className="queue-submit-form" onSubmit={handleSubmit}>
          <section className="space-y-4 rounded-[1.5rem] border border-mm-border/80 bg-white/70 p-5 shadow-sm dark:bg-slate-950/20">
            <div>
              <h3 className="text-lg font-semibold text-slate-950 dark:text-white">Objective</h3>
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                Define the overall task. Use steps below when you need a structured multi-phase run.
              </p>
            </div>

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
          </section>

          {taskTemplateCatalogEnabled ? (
            <section className="space-y-4 rounded-[1.5rem] border border-mm-border/80 bg-white/70 p-5 shadow-sm dark:bg-slate-950/20">
              <div>
                <h3 className="text-lg font-semibold text-slate-950 dark:text-white">Presets</h3>
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                  Expand a saved preset into real task steps, then edit the generated steps before
                  submission.
                </p>
              </div>

              <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
                <label>
                  Preset
                  <select
                    name="preset"
                    value={selectedPresetKey}
                    onChange={(event) => setSelectedPresetKey(event.target.value)}
                  >
                    <option value="">Select a preset</option>
                    {(templateOptionsQuery.data || []).map((option) => (
                      <option key={option.key} value={option.key}>
                        {option.title} ({option.scope})
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  Apply Mode
                  <select
                    name="presetApplyMode"
                    value={presetApplyMode}
                    onChange={(event) =>
                      setPresetApplyMode(event.target.value as PresetApplyMode)
                    }
                  >
                    <option value="append">Append Steps</option>
                    <option value="replace">Replace Steps</option>
                  </select>
                </label>
              </div>

              {selectedPresetDetailQuery.data?.inputs && selectedPresetDetailQuery.data.inputs.length > 0 ? (
                <div className="grid gap-4 md:grid-cols-2">
                  {selectedPresetDetailQuery.data.inputs.map((definition) => {
                    const value = presetInputs[definition.name];
                    if (definition.type === 'boolean') {
                      return (
                        <label key={definition.name} className="checkbox">
                          <input
                            type="checkbox"
                            checked={Boolean(value)}
                            onChange={(event) =>
                              setPresetInputs((current) => ({
                                ...current,
                                [definition.name]: event.target.checked,
                              }))
                            }
                          />
                          <span>{definition.label}</span>
                        </label>
                      );
                    }
                    if (definition.type === 'enum') {
                      return (
                        <label key={definition.name}>
                          {definition.label}
                          <select
                            value={String(value ?? '')}
                            onChange={(event) =>
                              setPresetInputs((current) => ({
                                ...current,
                                [definition.name]: event.target.value,
                              }))
                            }
                          >
                            <option value="">Select</option>
                            {(definition.options || []).map((option) => (
                              <option key={option} value={option}>
                                {option}
                              </option>
                            ))}
                          </select>
                        </label>
                      );
                    }
                    if (definition.type === 'textarea' || definition.type === 'markdown') {
                      return (
                        <label key={definition.name} className="md:col-span-2">
                          {definition.label}
                          <textarea
                            value={String(value ?? '')}
                            onChange={(event) =>
                              setPresetInputs((current) => ({
                                ...current,
                                [definition.name]: event.target.value,
                              }))
                            }
                          />
                        </label>
                      );
                    }
                    return (
                      <label key={definition.name}>
                        {definition.label}
                        <input
                          value={String(value ?? '')}
                          onChange={(event) =>
                            setPresetInputs((current) => ({
                              ...current,
                              [definition.name]: event.target.value,
                            }))
                          }
                        />
                      </label>
                    );
                  })}
                </div>
              ) : null}

              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  className="queue-submit-primary"
                  onClick={handleApplyPreset}
                  disabled={isApplyingPreset || !selectedPreset}
                >
                  {isApplyingPreset ? 'Applying...' : 'Apply Preset'}
                </button>
                {templateOptionsQuery.isLoading ? (
                  <span className="small">Loading presets...</span>
                ) : null}
                {templateOptionsQuery.isError ? (
                  <span className="small text-rose-600 dark:text-rose-300">
                    Failed to load presets.
                  </span>
                ) : null}
                {selectedPresetDetailQuery.isLoading ? (
                  <span className="small">Loading preset inputs...</span>
                ) : null}
              </div>

              <p className={`small${presetMessage ? ' text-slate-700 dark:text-slate-300' : ''}`}>
                {presetMessage ||
                  (selectedPreset
                    ? `${selectedPreset.description || 'Preset ready to apply.'}`
                    : 'Select a preset to expand it into steps.')}
              </p>
            </section>
          ) : null}

          <section className="space-y-4 rounded-[1.5rem] border border-mm-border/80 bg-white/70 p-5 shadow-sm dark:bg-slate-950/20">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-950 dark:text-white">Steps</h3>
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                  Add as many steps as you need. Each step can carry its own skill override.
                </p>
              </div>
              <button type="button" className="secondary" onClick={addStep}>
                Add Step
              </button>
            </div>

            {steps.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-mm-border/90 px-4 py-6 text-sm text-slate-600 dark:text-slate-400">
                No steps defined yet. Use presets or add a manual step.
              </div>
            ) : null}

            <datalist id={SKILL_OPTIONS_DATALIST_ID}>
              {(skillsQuery.data || []).map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>

            <div className="space-y-4">
              {steps.map((step, index) => (
                <article
                  key={step.localId}
                  className="space-y-4 rounded-2xl border border-mm-border/80 bg-slate-50/80 p-4 dark:bg-slate-900/40"
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h4 className="text-base font-semibold text-slate-950 dark:text-white">
                        Step {index + 1}
                      </h4>
                      {step.originLabel ? (
                        <p className="small">{step.originLabel}</p>
                      ) : null}
                    </div>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => removeStep(step.localId)}
                    >
                      Remove
                    </button>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <label>
                      Step Title
                      <input
                        value={step.title}
                        onChange={(event) =>
                          updateStep(step.localId, { title: event.target.value })
                        }
                      />
                    </label>

                    <label>
                      Step Skill
                      <input
                        list={SKILL_OPTIONS_DATALIST_ID}
                        value={step.skillId}
                        onChange={(event) =>
                          updateStep(step.localId, {
                            skillId: event.target.value,
                            skillArgs: {},
                            requiredCapabilities: [],
                          })
                        }
                      />
                    </label>

                    <label className="md:col-span-2">
                      Step Instructions
                      <textarea
                        data-step-field="instructions"
                        data-step-index={String(index + 1)}
                        value={step.instructions}
                        onChange={(event) =>
                          updateStep(step.localId, { instructions: event.target.value })
                        }
                      />
                    </label>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="space-y-4 rounded-[1.5rem] border border-mm-border/80 bg-white/70 p-5 shadow-sm dark:bg-slate-950/20">
            <div>
              <h3 className="text-lg font-semibold text-slate-950 dark:text-white">
                Execution Settings
              </h3>
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                These settings apply to the overall task submission.
              </p>
            </div>

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
                  list={SKILL_OPTIONS_DATALIST_ID}
                  value={skill}
                  onChange={(event) => setSkill(event.target.value)}
                />
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

            <label className="checkbox">
              <input
                type="checkbox"
                checked={proposeTasks}
                onChange={(event) => setProposeTasks(event.target.checked)}
              />
              <span>Generate follow-up proposals after the run</span>
            </label>
          </section>

          <div className="actions flex flex-wrap items-center gap-3">
            <button type="submit" className="queue-submit-primary" disabled={isSubmitting}>
              {isSubmitting ? 'Submitting...' : 'Create'}
            </button>
            <span className="small">
              Runtime picker stays worker-only; Temporal routing remains backend-managed.
            </span>
          </div>

          <p
            id="queue-submit-message"
            className={`queue-submit-message${submitMessage ? ' notice error' : ''}`}
          >
            {submitMessage || ''}
          </p>
        </form>
      </div>
    </div>
  );
}

mountPage(TaskCreatePage);
