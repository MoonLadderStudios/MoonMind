export type TaskSubmitPageMode = 'create' | 'edit' | 'rerun';
const PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE = 'pr_with_merge_automation';

function isPrPublishMode(value: string | null | undefined): boolean {
  const normalized = String(value || '').trim().toLowerCase();
  return normalized === 'pr' || normalized === PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE;
}

export type TaskSubmitPageIntent =
  | 'create'
  | 'edit'
  | 'rerun'
  | 'edit-for-rerun'
  | 'comparison';

export type TaskSubmitPageModeResolution = {
  mode: TaskSubmitPageMode;
  intent: TaskSubmitPageIntent;
  executionId: string | null;
};

export type TemporalTaskEditingActions = {
  canUpdateInputs?: boolean;
  canEditForRerun?: boolean;
  canRerun?: boolean;
  disabledReasons?: Record<string, string>;
};

export type TemporalTaskEditingExecutionContract = {
  workflowId: string;
  runId?: string | null;
  temporalRunId?: string | null;
  workflowType?: string | null;
  state?: string | null;
  rawState?: string | null;
  temporalStatus?: string | null;
  inputParameters?: Record<string, unknown>;
  inputArtifactRef?: string | null;
  taskInputSnapshot?: {
    available?: boolean;
    artifactRef?: string | null;
    snapshotVersion?: number | null;
    sourceKind?: string | null;
    reconstructionMode?: string | null;
    disabledReasons?: Record<string, string>;
    fallbackEvidenceRefs?: string[];
  } | null;
  planArtifactRef?: string | null;
  targetRuntime?: string | null;
  profileId?: string | null;
  model?: string | null;
  requestedModel?: string | null;
  resolvedModel?: string | null;
  effort?: string | null;
  modelTier?: number | null;
  tierFallback?: string | null;
  repository?: string | null;
  branch?: string | null;
  startingBranch?: string | null;
  targetBranch?: string | null;
  publishMode?: string | null;
  targetSkill?: string | null;
  taskSkills?: string[] | null;
  actions?: TemporalTaskEditingActions;
};

export type TemporalTaskInputAttachmentRef = {
  artifactId: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
};

export type TemporalSubmissionDraftStepType = 'tool' | 'skill' | 'preset';

export type TemporalSubmissionDraftToolPayload = {
  id?: string;
  name?: string;
  version?: string;
  type?: string;
  kind?: string;
  inputs?: Record<string, unknown>;
  args?: Record<string, unknown>;
  requiredCapabilities?: unknown;
  [key: string]: unknown;
};

export type TemporalSubmissionDraftSkillPayload = {
  id?: string;
  name?: string;
  inputs?: Record<string, unknown>;
  args?: Record<string, unknown>;
  inputContractDigest?: string;
  currentInputContractDigest?: string;
  requiredCapabilities?: unknown;
  [key: string]: unknown;
};

export type TemporalSubmissionDraftPresetPayload = {
  id?: string;
  slug?: string;
  name?: string;
  inputs?: Record<string, unknown>;
  [key: string]: unknown;
};

export type TemporalSubmissionDraft = {
  runtime: string | null;
  providerProfile: string | null;
  omnigentExecutionTargetRef: string | null;
  omnigentLaunchPolicyRef: string | null;
  model: string | null;
  effort: string | null;
  modelTier: number | null;
  tierFallback: 'clamp' | 'strict' | null;
  repository: string | null;
  branch: string | null;
  legacyBranchWarning: string | null;
  publishMode: string | null;
  mergeAutomationEnabled: boolean;
  reportOutputEnabled: boolean;
  taskInstructions: string;
  runtimeCommand?: Record<string, unknown>;
  primarySkill: string | null;
  inputAttachments: TemporalTaskInputAttachmentRef[];
  steps: Array<{
    id: string;
    title: string;
    instructions: string;
    runtime?: {
      mode?: string | null;
      model?: string | null;
      effort?: string | null;
      modelTier?: number | null;
      tierFallback?: string | null;
      profileId?: string | null;
      providerProfile?: string | null;
      executionProfileRef?: string | null;
    };
    stepType: TemporalSubmissionDraftStepType;
    tool?: TemporalSubmissionDraftToolPayload;
    skill?: TemporalSubmissionDraftSkillPayload;
    preset?: TemporalSubmissionDraftPresetPayload;
    skillId: string;
    skillArgs: Record<string, unknown>;
    skillInputContractDigest: string;
    skillRequiredCapabilities: string[];
    templateStepId: string;
    templateInstructions: string;
    inputAttachments?: TemporalTaskInputAttachmentRef[];
    templateAttachments?: Array<{
      artifactId: string;
      filename: string;
      contentType: string;
      sizeBytes: number;
    }>;
    storyOutput?: Record<string, unknown>;
    jiraOrchestration?: Record<string, unknown>;
    runtimeCommand?: Record<string, unknown>;
  }>;
  appliedTemplates: Array<{
    slug: string;
    inputs: Record<string, unknown>;
    stepIds: string[];
    appliedAt: string;
    capabilities: string[];
  }>;
};

export type TemporalTaskEditUpdateName = 'UpdateInputs' | 'RequestRerun';

export type TemporalTaskEditingTelemetryEvent =
  | 'detail_edit_click'
  | 'detail_compare_click'
  | 'detail_rerun_click'
  | 'draft_reconstruction_success'
  | 'draft_reconstruction_failure'
  | 'update_submit_attempt'
  | 'update_submit_result'
  | 'rerun_submit_attempt'
  | 'rerun_submit_result';

export type TemporalTaskEditingTelemetryPayload = {
  event: TemporalTaskEditingTelemetryEvent;
  mode?: TaskSubmitPageIntent | 'detail';
  workflowId?: string | null;
  updateName?: TemporalTaskEditUpdateName;
  result?: 'success' | 'failure';
  reason?: string | null;
  applied?: string | null;
};

export type TemporalArtifactEditUpdatePayload = {
  updateName: TemporalTaskEditUpdateName;
  inputArtifactRef?: string;
  parametersPatch?: Record<string, unknown>;
};

export type RuntimeCommandVersionConfig = {
  hintCatalogVersion?: unknown;
};

export function buildRuntimeCommandVersionWarnings(
  runtimeCommand: Record<string, unknown> | null | undefined,
  currentConfig: RuntimeCommandVersionConfig | null | undefined,
): string[] {
  const command = objectValue(runtimeCommand);
  const config = objectValue(currentConfig);
  if (Object.keys(command).length === 0 || Object.keys(config).length === 0) {
    return [];
  }

  const warnings: string[] = [];
  const storedHintCatalogVersion = stringValue(command.hintCatalogVersion);
  const currentHintCatalogVersion = stringValue(config.hintCatalogVersion);
  if (
    storedHintCatalogVersion &&
    currentHintCatalogVersion &&
    storedHintCatalogVersion !== currentHintCatalogVersion
  ) {
    warnings.push(
      `Runtime command hint catalog version changed from ${storedHintCatalogVersion} to ${currentHintCatalogVersion}.`,
    );
  }

  return warnings;
}

export function buildTemporalArtifactEditUpdatePayload({
  updateName,
  inputArtifactRef,
  parametersPatch,
}: {
  updateName: TemporalTaskEditUpdateName;
  inputArtifactRef?: string | null;
  parametersPatch?: Record<string, unknown> | null;
}): TemporalArtifactEditUpdatePayload {
  const normalizedArtifactRef = String(inputArtifactRef || '').trim();
  return {
    updateName,
    ...(normalizedArtifactRef
      ? { inputArtifactRef: normalizedArtifactRef }
      : {}),
    ...(parametersPatch ? { parametersPatch } : {}),
  };
}

export function recordTemporalTaskEditingClientEvent(
  payload: TemporalTaskEditingTelemetryPayload,
): void {
  const boundedPayload = {
    event: payload.event,
    ...(payload.mode ? { mode: payload.mode } : {}),
    ...(payload.workflowId ? { workflowId: payload.workflowId } : {}),
    ...(payload.updateName ? { updateName: payload.updateName } : {}),
    ...(payload.result ? { result: payload.result } : {}),
    ...(payload.reason ? { reason: payload.reason.slice(0, 120) } : {}),
    ...(payload.applied ? { applied: payload.applied.slice(0, 80) } : {}),
  };

  try {
    window.dispatchEvent(
      new CustomEvent('moonmind:temporal-task-editing', {
        detail: boundedPayload,
      }),
    );
  } catch {
    // Telemetry must never affect task editing behavior.
  }

  try {
    console.info('moonmind.temporal_task_editing', boundedPayload);
  } catch {
    // Console availability varies in embedded browser contexts.
  }
}

export function taskCreateHref(): string {
  return '/workflows/new';
}

export function taskEditHref(workflowId: string): string {
  return `${taskCreateHref()}?editExecutionId=${encodeURIComponent(workflowId)}`;
}

export function taskEditForRerunHref(workflowId: string): string {
  return `${taskCreateHref()}?rerunExecutionId=${encodeURIComponent(workflowId)}&mode=edit`;
}

export function taskCompareHref(workflowId: string): string {
  return `${taskCreateHref()}?rerunExecutionId=${encodeURIComponent(workflowId)}&mode=compare`;
}

export function resolveTaskSubmitPageMode(
  search: string | URLSearchParams,
): TaskSubmitPageModeResolution {
  const params =
    typeof search === 'string' ? new URLSearchParams(search) : search;
  const rerunExecutionId = String(params.get('rerunExecutionId') || '').trim();
  if (rerunExecutionId) {
    const mode = String(params.get('mode') || '').trim().toLowerCase();
    if (mode === 'edit') {
      return {
        mode: 'rerun',
        intent: 'edit-for-rerun',
        executionId: rerunExecutionId,
      };
    }
    if (mode === 'compare') {
      return {
        mode: 'rerun',
        intent: 'comparison',
        executionId: rerunExecutionId,
      };
    }
    return { mode: 'rerun', intent: 'rerun', executionId: rerunExecutionId };
  }
  const editExecutionId = String(params.get('editExecutionId') || '').trim();
  if (editExecutionId) {
    return { mode: 'edit', intent: 'edit', executionId: editExecutionId };
  }
  return { mode: 'create', intent: 'create', executionId: null };
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringValue(...values: unknown[]): string {
  for (const value of values) {
    const normalized = String(value ?? '').trim();
    if (normalized) {
      return normalized;
    }
  }
  return '';
}

function positiveIntegerValue(...values: unknown[]): number | null {
  for (const value of values) {
    const parsed = Number.parseInt(String(value ?? '').trim(), 10);
    if (Number.isInteger(parsed) && parsed >= 1) {
      return parsed;
    }
  }
  return null;
}

function tierFallbackValue(...values: unknown[]): 'clamp' | 'strict' | null {
  for (const value of values) {
    const normalized = String(value ?? '').trim().toLowerCase();
    if (normalized === 'clamp' || normalized === 'strict') {
      return normalized;
    }
  }
  return null;
}

function stepInstructions(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => objectValue(entry))
    .map((entry) => stringValue(entry.instructions))
    .filter(Boolean);
}

function stringArrayValue(...values: unknown[]): string[] {
  for (const value of values) {
    if (!Array.isArray(value)) {
      continue;
    }
    const normalized = value
      .map((item) => String(item ?? '').trim())
      .filter(Boolean);
    if (normalized.length > 0) {
      return normalized;
    }
  }
  return [];
}

function attachmentRefsValue(...values: unknown[]): Array<{
  artifactId: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
}> {
  for (const value of values) {
    if (!Array.isArray(value)) {
      continue;
    }
    const normalized = value
      .map((item) => {
        const attachment = objectValue(item);
        const artifactId = stringValue(
          attachment.artifactId,
          attachment.artifact_id,
        );
        const filename = stringValue(attachment.filename, attachment.name);
        const contentType = stringValue(
          attachment.contentType,
          attachment.content_type,
        );
        const rawSize = attachment.sizeBytes ?? attachment.size_bytes;
        const sizeBytes = Math.max(0, Number(rawSize) || 0);
        if (!artifactId && !filename) {
          return null;
        }
        return {
          artifactId,
          filename,
          contentType,
          sizeBytes,
        };
      })
      .filter(
        (
          item,
        ): item is {
          artifactId: string;
          filename: string;
          contentType: string;
          sizeBytes: number;
        } => Boolean(item),
      );
    if (normalized.length > 0) {
      return normalized;
    }
  }
  return [];
}

function firstObjectValue(...values: unknown[]): Record<string, unknown> {
  for (const value of values) {
    const normalized = objectValue(value);
    if (Object.keys(normalized).length > 0) {
      return normalized;
    }
  }
  return {};
}

function taskInstructionsFrom(...tasks: Record<string, unknown>[]): string {
  for (const task of tasks) {
    const instructions = [
      stringValue(task.instructions),
      ...stepInstructions(task.steps),
    ].filter(Boolean);
    if (instructions.length > 0) {
      return instructions.join('\n\n');
    }
  }
  return '';
}

function normalizeAttachmentRefs(value: unknown): TemporalTaskInputAttachmentRef[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => objectValue(entry))
    .map((entry) => ({
      artifactId: stringValue(entry.artifactId, entry.artifact_id),
      filename: stringValue(entry.filename, entry.name),
      contentType: stringValue(entry.contentType, entry.content_type),
      sizeBytes: Math.max(0, Number(entry.sizeBytes ?? entry.size_bytes ?? 0) || 0),
    }))
    .filter((entry) => entry.artifactId && entry.filename && entry.contentType);
}

function compactAttachmentBindingKey(
  ref: Record<string, unknown>,
): string | null {
  const artifactId = stringValue(ref.artifactId, ref.artifact_id);
  if (!artifactId) {
    return null;
  }
  const targetKind = stringValue(
    ref.targetKind,
    ref.target_kind,
    ref.target,
  ).toLowerCase();
  if (targetKind === 'objective' || targetKind === 'task') {
    return `objective:${artifactId}`;
  }
  if (targetKind === 'step') {
    const stepId = stringValue(ref.stepId, ref.step_id);
    if (stepId) {
      return `step:id:${stepId}:${artifactId}`;
    }
    const stepOrdinalValue = Number(ref.stepOrdinal ?? ref.step_ordinal);
    if (Number.isInteger(stepOrdinalValue) && stepOrdinalValue >= 0) {
      return `step:ordinal:${stepOrdinalValue}:${artifactId}`;
    }
    return null;
  }
  return null;
}

function stepAttachmentBindingKeys(
  step: TemporalSubmissionDraft['steps'][number],
  stepOrdinal: number,
): string[] {
  const refs = step.inputAttachments || step.templateAttachments || [];
  return refs.flatMap((ref) => {
    const keys = [`step:ordinal:${stepOrdinal}:${ref.artifactId}`];
    if (step.id) {
      keys.push(`step:id:${step.id}:${ref.artifactId}`);
    }
    return keys;
  });
}

function draftStepFrom(value: unknown): TemporalSubmissionDraft['steps'][number] | null {
  const step = objectValue(value);
  if (Object.keys(step).length === 0) {
    return null;
  }

  const tool = objectValue(step.tool);
  const skill = objectValue(step.skill);
  const preset = objectValue(step.preset);
  const rawStepType = stringValue(step.stepType, step.type).toLowerCase();
  const legacyToolType = stringValue(tool.type, tool.kind).toLowerCase();
  const stepType: TemporalSubmissionDraftStepType =
    rawStepType === 'tool' || rawStepType === 'skill' || rawStepType === 'preset'
      ? rawStepType
      : Object.keys(preset).length > 0
        ? 'preset'
        : Object.keys(tool).length > 0 && legacyToolType !== 'skill'
          ? 'tool'
          : 'skill';
  const instructions = stringValue(step.instructions);
  const id = stringValue(step.id);
  const templateStepId = stringValue(
    step.templateStepId,
    step.template_step_id,
    id.startsWith('tpl:') ? id : '',
  );
  const storyOutput = firstObjectValue(step.storyOutput, step.story_output);
  const jiraOrchestration = firstObjectValue(
    step.jiraOrchestration,
    step.jira_orchestration,
  );
  const runtimeCommand = firstObjectValue(
    step.runtimeCommand,
    step.runtime_command,
  );
  const runtime = firstObjectValue(step.runtime);
  const inputAttachments = normalizeAttachmentRefs(step.inputAttachments);
  const templateAttachments = attachmentRefsValue(
    step.templateAttachments,
    step.template_attachments,
    step.inputAttachments,
    step.input_attachments,
    step.attachments,
  );
  const result = {
    id,
    title: stringValue(step.title),
    instructions,
    ...(Object.keys(runtime).length > 0 ? { runtime } : {}),
    stepType,
    ...(Object.keys(tool).length > 0 ? { tool } : {}),
    ...(Object.keys(skill).length > 0 ? { skill } : {}),
    ...(Object.keys(preset).length > 0 ? { preset } : {}),
    skillId: stringValue(tool.name, tool.id, skill.id, skill.name),
    skillArgs: firstObjectValue(tool.inputs, tool.args, skill.inputs, skill.args),
    skillInputContractDigest: stringValue(
      skill.inputContractDigest,
      tool.inputContractDigest,
    ),
    skillRequiredCapabilities: stringArrayValue(
      tool.requiredCapabilities,
      skill.requiredCapabilities,
    ),
    ...(Object.keys(runtime).length > 0
      ? {
          runtime: {
            ...(stringValue(runtime.mode, runtime.targetRuntime)
              ? { mode: stringValue(runtime.mode, runtime.targetRuntime) }
              : {}),
            ...(stringValue(runtime.model) ? { model: stringValue(runtime.model) } : {}),
            ...(stringValue(runtime.effort) ? { effort: stringValue(runtime.effort) } : {}),
            ...(positiveIntegerValue(runtime.modelTier) != null
              ? { modelTier: positiveIntegerValue(runtime.modelTier) }
              : {}),
            ...(tierFallbackValue(runtime.tierFallback)
              ? { tierFallback: tierFallbackValue(runtime.tierFallback) }
              : {}),
            ...(stringValue(runtime.profileId)
              ? { profileId: stringValue(runtime.profileId) }
              : {}),
            ...(stringValue(runtime.providerProfile)
              ? { providerProfile: stringValue(runtime.providerProfile) }
              : {}),
          },
        }
      : {}),
    templateStepId,
    templateInstructions: stringValue(
      step.templateInstructions,
      step.template_instructions,
      templateStepId ? instructions : '',
    ),
    ...(inputAttachments.length > 0 ? { inputAttachments } : {}),
    ...(templateAttachments.length > 0 ? { templateAttachments } : {}),
    ...(Object.keys(storyOutput).length > 0 ? { storyOutput } : {}),
    ...(Object.keys(jiraOrchestration).length > 0 ? { jiraOrchestration } : {}),
    ...(Object.keys(runtimeCommand).length > 0 ? { runtimeCommand } : {}),
  };

  const hasContent =
    result.id ||
    result.title ||
    result.instructions ||
    Object.keys(runtime).length > 0 ||
    result.stepType !== 'skill' ||
    result.skillId ||
    Object.keys(result.skillArgs).length > 0 ||
    result.skillInputContractDigest ||
    result.skillRequiredCapabilities.length > 0 ||
    Boolean(result.runtime && Object.keys(result.runtime).length > 0) ||
    result.templateStepId ||
    result.templateInstructions ||
    inputAttachments.length > 0 ||
    templateAttachments.length > 0 ||
    Object.keys(storyOutput).length > 0 ||
    Object.keys(jiraOrchestration).length > 0 ||
    Object.keys(runtimeCommand).length > 0;
  return hasContent ? result : null;
}

function draftStepsFromTask(
  task: Record<string, unknown>,
): TemporalSubmissionDraft['steps'] {
  const rawSteps = Array.isArray(task.steps) ? task.steps : [];
  const steps = rawSteps
    .map((entry) => draftStepFrom(entry))
    .filter((entry): entry is TemporalSubmissionDraft['steps'][number] =>
      Boolean(entry),
    );
  if (steps.length === 0) {
    return [];
  }

  return steps;
}

function selectDraftSteps(
  taskSteps: TemporalSubmissionDraft['steps'],
  artifactTaskSteps: TemporalSubmissionDraft['steps'],
): TemporalSubmissionDraft['steps'] {
  const instructionCount = (steps: TemporalSubmissionDraft['steps']) =>
    steps.filter((step) => stringValue(step.instructions)).length;
  const artifactHasAttachments = artifactTaskSteps.some(
    (step) =>
      (step.inputAttachments || []).length > 0 ||
      (step.templateAttachments || []).length > 0,
  );
  const taskHasAttachments = taskSteps.some(
    (step) =>
      (step.inputAttachments || []).length > 0 ||
      (step.templateAttachments || []).length > 0,
  );
  if (artifactHasAttachments && !taskHasAttachments) {
    return artifactTaskSteps;
  }
  if (artifactTaskSteps.length > taskSteps.length) {
    return artifactTaskSteps;
  }
  if (instructionCount(artifactTaskSteps) > instructionCount(taskSteps)) {
    return artifactTaskSteps;
  }
  return taskSteps.length > 0 ? taskSteps : artifactTaskSteps;
}

function nullableStringValue(...values: unknown[]): string | null {
  return stringValue(...values) || null;
}

function booleanEnabledValue(...values: unknown[]): boolean {
  return values.some((value) => {
    if (value === true) {
      return true;
    }
    if (typeof value !== 'string') {
      return false;
    }
    return ['true', '1', 'yes', 'on'].includes(value.trim().toLowerCase());
  });
}

function skillSelectorNames(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => stringValue(item)).filter(Boolean);
  }

  const selectors = objectValue(value);
  const include = selectors.include;
  if (!Array.isArray(include)) {
    return [];
  }
  return include
    .map((entry) => stringValue(objectValue(entry).name))
    .filter(Boolean);
}

function normalizeAppliedTemplates(
  value: unknown,
): TemporalSubmissionDraft['appliedTemplates'] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => objectValue(entry))
    .map((entry) => ({
      slug: stringValue(entry.slug),
      inputs: objectValue(entry.inputs),
      stepIds: Array.isArray(entry.stepIds)
        ? entry.stepIds.map((item) => String(item || '').trim()).filter(Boolean)
        : [],
      appliedAt: stringValue(entry.appliedAt),
      capabilities: Array.isArray(entry.capabilities)
        ? entry.capabilities
            .map((item) => String(item || '').trim())
            .filter(Boolean)
        : [],
    }))
    .filter((entry) => entry.slug);
}

function workflowRecord(source: Record<string, unknown>): Record<string, unknown> {
  return objectValue(source.workflow);
}

function assertSnapshotAttachmentBindings(
  artifactParams: Record<string, unknown>,
  artifactTask: Record<string, unknown>,
  artifactTaskSteps: TemporalSubmissionDraft['steps'],
): void {
  const snapshotDraft = objectValue(artifactParams.draft);
  const authoredTaskInput = objectValue(snapshotDraft.authoredTaskInput);
  const compactRefSources = [
    artifactParams.attachmentRefs,
    snapshotDraft.attachmentRefs,
    authoredTaskInput.attachmentRefs,
  ];
  const compactRefs = compactRefSources.flatMap((source) =>
    Array.isArray(source) ? source.map((entry) => objectValue(entry)) : [],
  );
  if (compactRefs.length === 0) {
    return;
  }
  const compactKeys = compactRefs.map(compactAttachmentBindingKey);
  if (compactKeys.some((key) => key === null)) {
    throw new Error(
      'Attachment bindings could not be reconstructed from this execution.',
    );
  }
  const boundKeys = new Set([
    ...normalizeAttachmentRefs(artifactTask.inputAttachments).map(
      (ref) => `objective:${ref.artifactId}`,
    ),
    ...artifactTaskSteps.flatMap(stepAttachmentBindingKeys),
  ]);
  const unbound = compactKeys.filter(
    (key): key is string => key !== null && !boundKeys.has(key),
  );
  if (unbound.length > 0) {
    throw new Error(
      'Attachment bindings could not be reconstructed from this execution.',
    );
  }
}

function snapshotDraftTask(
  snapshotDraft: Record<string, unknown>,
): Record<string, unknown> {
  const nestedWorkflow = objectValue(snapshotDraft.workflow);
  if (Object.keys(nestedWorkflow).length > 0) {
    return nestedWorkflow;
  }
  const primarySkill = objectValue(snapshotDraft.primarySkill);
  const publish = objectValue(snapshotDraft.publish);
  const task: Record<string, unknown> = {
    ...(stringValue(snapshotDraft.instructions)
      ? { instructions: stringValue(snapshotDraft.instructions) }
      : {}),
    ...(Object.keys(firstObjectValue(snapshotDraft.runtimeCommand, snapshotDraft.runtime_command)).length > 0
      ? {
          runtimeCommand: firstObjectValue(
            snapshotDraft.runtimeCommand,
            snapshotDraft.runtime_command,
          ),
        }
      : {}),
    ...(Object.keys(primarySkill).length > 0
      ? {
          tool: {
            type: 'skill',
            name: stringValue(primarySkill.name),
            inputs: objectValue(primarySkill.inputs),
          },
          skill: {
            id: stringValue(primarySkill.name),
            inputs: objectValue(primarySkill.inputs),
          },
          inputs: objectValue(primarySkill.inputs),
        }
      : {}),
    ...(Object.keys(publish).length > 0 ? { publish } : {}),
    ...(Array.isArray(snapshotDraft.appliedTemplates)
      ? { appliedStepTemplates: snapshotDraft.appliedTemplates }
      : {}),
    ...(Array.isArray(snapshotDraft.steps) ? { steps: snapshotDraft.steps } : {}),
  };
  const branch = stringValue(snapshotDraft.branch);
  const startingBranch = stringValue(snapshotDraft.startingBranch);
  const targetBranch = stringValue(snapshotDraft.targetBranch);
  if (branch || startingBranch || targetBranch) {
    task.git = {
      ...(branch ? { branch } : {}),
      ...(startingBranch ? { startingBranch } : {}),
      ...(targetBranch ? { targetBranch } : {}),
    };
  }
  const runtime = stringValue(snapshotDraft.runtime);
  const model = stringValue(snapshotDraft.model);
  const effort = stringValue(snapshotDraft.effort);
  const modelTier = positiveIntegerValue(snapshotDraft.modelTier);
  const tierFallback = tierFallbackValue(snapshotDraft.tierFallback);
  const providerProfile = stringValue(snapshotDraft.providerProfile);
  if (runtime || model || effort || providerProfile || modelTier != null || tierFallback) {
    task.runtime = {
      ...(runtime ? { mode: runtime } : {}),
      ...(model ? { model } : {}),
      ...(effort ? { effort } : {}),
      ...(modelTier != null ? { modelTier } : {}),
      ...(tierFallback ? { tierFallback } : {}),
      ...(providerProfile ? { profileId: providerProfile } : {}),
    };
  }
  return task;
}

function normalizeLegacyBranchDraft({
  publishMode,
  branch,
  startingBranch,
  targetBranch,
}: {
  publishMode: string | null;
  branch: string | null;
  startingBranch: string | null;
  targetBranch: string | null;
}): { branch: string | null; warning: string | null } {
  if (branch) {
    return { branch, warning: null };
  }
  if (startingBranch && (!targetBranch || targetBranch === startingBranch)) {
    return { branch: startingBranch, warning: null };
  }
  if (!startingBranch && targetBranch) {
    return {
      branch: null,
      warning:
        "This older workflow only stored a target branch. The new form cannot use targetBranch as the active branch, so choose a branch before saving or rerunning.",
    };
  }
  if (startingBranch && targetBranch && isPrPublishMode(publishMode)) {
    return { branch: startingBranch, warning: null };
  }
  if (startingBranch && targetBranch) {
    return {
      branch: startingBranch,
      warning:
        "This older workflow used separate starting and target branches. The new form submits one branch, so review it before saving or rerunning.",
    };
  }
  return { branch: null, warning: null };
}

export function buildTemporalSubmissionDraftFromExecution(
  execution: TemporalTaskEditingExecutionContract,
  artifactInput?: unknown,
): TemporalSubmissionDraft {
  const params = objectValue(execution.inputParameters);
  const artifactParams = objectValue(artifactInput);
  const snapshotDraft = objectValue(artifactParams.draft);
  const task = workflowRecord(params);
  const artifactTask = Object.keys(snapshotDraft).length > 0
    ? snapshotDraftTask(snapshotDraft)
    : workflowRecord(artifactParams);
  const runtime = objectValue(task.runtime);
  const artifactRuntime = objectValue(artifactTask.runtime);
  const omnigent = objectValue(params.omnigent);
  const artifactOmnigent = objectValue(artifactParams.omnigent);
  const git = objectValue(task.git);
  const artifactGit = objectValue(artifactTask.git);
  const publish = objectValue(task.publish);
  const artifactPublish = objectValue(artifactTask.publish);
  const mergeAutomation = objectValue(params.mergeAutomation);
  const artifactMergeAutomation = objectValue(artifactParams.mergeAutomation);
  const taskMergeAutomation = objectValue(task.mergeAutomation);
  const artifactTaskMergeAutomation = objectValue(artifactTask.mergeAutomation);
  const reportOutput = objectValue(params.reportOutput);
  const artifactReportOutput = objectValue(artifactParams.reportOutput);
  const taskReportOutput = objectValue(task.reportOutput);
  const artifactTaskReportOutput = objectValue(artifactTask.reportOutput);
  const snapshotReportOutput = objectValue(snapshotDraft.reportOutput);
  const tool = objectValue(task.tool);
  const skill = objectValue(task.skill);
  const artifactTool = objectValue(artifactTask.tool);
  const artifactSkill = objectValue(artifactTask.skill);
  const taskSteps = draftStepsFromTask(task);
  const artifactTaskSteps = draftStepsFromTask(artifactTask);
  const runtimeCommand = firstObjectValue(
    task.runtimeCommand,
    task.runtime_command,
    artifactTask.runtimeCommand,
    artifactTask.runtime_command,
  );
  assertSnapshotAttachmentBindings(
    artifactParams,
    artifactTask,
    artifactTaskSteps,
  );

  const artifactRepository = stringValue(artifactParams.repository);
  const taskSkills = skillSelectorNames(task.skills);
  const executionTaskSkills = Array.isArray(execution.taskSkills)
    ? execution.taskSkills
    : [];
  const artifactTaskSkills = skillSelectorNames(artifactTask.skills);
  const publishMode = nullableStringValue(
    execution.publishMode,
    params.publishMode,
    publish.mode,
    artifactPublish.mode,
  );
  const legacyBranchDraft = normalizeLegacyBranchDraft({
    publishMode,
    branch: nullableStringValue(
      snapshotDraft.branch,
      execution.branch,
      task.branch,
      git.branch,
      params.branch,
      artifactTask.branch,
      artifactGit.branch,
    ),
    startingBranch: nullableStringValue(
      snapshotDraft.startingBranch,
      execution.startingBranch,
      task.startingBranch,
      git.startingBranch,
      params.startingBranch,
      artifactTask.startingBranch,
      artifactGit.startingBranch,
    ),
    targetBranch: nullableStringValue(
      snapshotDraft.targetBranch,
      execution.targetBranch,
      task.targetBranch,
      git.targetBranch,
      params.targetBranch,
      artifactTask.targetBranch,
      artifactGit.targetBranch,
    ),
  });

  const draft: TemporalSubmissionDraft = {
    runtime: nullableStringValue(
      snapshotDraft.runtime,
      execution.targetRuntime,
      params.targetRuntime,
      runtime.mode,
      artifactRuntime.mode,
      artifactParams.targetRuntime,
    ),
    providerProfile: nullableStringValue(
      snapshotDraft.providerProfile,
      execution.profileId,
      params.profileId,
      runtime.profileId,
      artifactRuntime.profileId,
    ),
    omnigentExecutionTargetRef: nullableStringValue(
      snapshotDraft.omnigentExecutionTargetRef,
      omnigent.executionTargetRef,
      artifactOmnigent.executionTargetRef,
    ),
    omnigentLaunchPolicyRef: nullableStringValue(
      snapshotDraft.omnigentLaunchPolicyRef,
      omnigent.launchPolicyRef,
      artifactOmnigent.launchPolicyRef,
    ),
    model: nullableStringValue(
      snapshotDraft.model,
      execution.model,
      execution.requestedModel,
      execution.resolvedModel,
      params.model,
      runtime.model,
      artifactRuntime.model,
    ),
    effort: nullableStringValue(
      snapshotDraft.effort,
      execution.effort,
      params.effort,
      runtime.effort,
      artifactRuntime.effort,
    ),
    modelTier: positiveIntegerValue(
      snapshotDraft.modelTier,
      execution.modelTier,
      params.modelTier,
      runtime.modelTier,
      artifactRuntime.modelTier,
    ),
    tierFallback: tierFallbackValue(
      snapshotDraft.tierFallback,
      execution.tierFallback,
      params.tierFallback,
      runtime.tierFallback,
      artifactRuntime.tierFallback,
    ),
    repository: nullableStringValue(
      snapshotDraft.repository,
      execution.repository,
      params.repository,
      task.repository,
      git.repository,
      artifactRepository,
      artifactTask.repository,
      artifactGit.repository,
    ),
    branch: legacyBranchDraft.branch,
    legacyBranchWarning: legacyBranchDraft.warning,
    publishMode,
    mergeAutomationEnabled: Boolean(
      publishMode === PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE ||
        mergeAutomation.enabled ||
        artifactMergeAutomation.enabled ||
        taskMergeAutomation.enabled ||
        artifactTaskMergeAutomation.enabled,
    ),
    reportOutputEnabled: booleanEnabledValue(
      snapshotReportOutput.enabled,
      reportOutput.enabled,
      artifactReportOutput.enabled,
      taskReportOutput.enabled,
      artifactTaskReportOutput.enabled,
    ),
    taskInstructions:
      Object.keys(snapshotDraft).length > 0
        ? taskInstructionsFrom(artifactTask, task)
        : taskInstructionsFrom(task, artifactTask),
    ...(Object.keys(runtimeCommand).length > 0 ? { runtimeCommand } : {}),
    primarySkill: nullableStringValue(
      execution.targetSkill,
      tool.name,
      skill.id,
      skill.name,
      taskSkills?.[0],
      executionTaskSkills[0],
      artifactTool.name,
      artifactSkill.id,
      artifactSkill.name,
      artifactTaskSkills[0],
    ),
    inputAttachments: (() => {
      const taskAttachments = normalizeAttachmentRefs(task.inputAttachments);
      const artifactAttachments = normalizeAttachmentRefs(artifactTask.inputAttachments);
      return taskAttachments.length > 0 ? taskAttachments : artifactAttachments;
    })(),
    steps: selectDraftSteps(taskSteps, artifactTaskSteps),
    appliedTemplates: normalizeAppliedTemplates(
      task.appliedStepTemplates || artifactTask.appliedStepTemplates,
    ),
  };

  const hasStepContent = draft.steps.some(
    (step) => step.instructions || step.skillId,
  );
  if (!draft.taskInstructions && !draft.primarySkill && !hasStepContent) {
    throw new Error(
      'Workflow instructions could not be reconstructed from this execution.',
    );
  }

  return draft;
}
