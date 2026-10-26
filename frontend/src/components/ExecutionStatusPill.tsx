import {
  formatStepExecutionStatusLabel,
  formatStepStatusLabel,
  isStepLedgerStatus,
  stepExecutionStatusPillProps,
  stepStatusPillProps,
} from '../status/stepStatus';
import {
  formatWorkflowStatusLabel,
  isWorkflowLifecycleStatus,
  workflowOutcomeOverride,
  workflowOutcomeOverridePresentation,
  workflowStatusPillProps,
} from '../status/workflowStatus';
import {
  formatIntegrationStatusLabel,
  integrationStatusPillProps,
  isIntegrationStatus,
} from '../status/integrationStatus';
import type { WorkflowStatusPillOptions } from '../status/workflowStatus';
import {
  CHECKPOINT_BRANCH_STATUS,
  CHECKPOINT_BRANCH_TURN_STATUS,
  DEPENDENCY_OUTCOME_STATUS,
  type StatusVocabulary,
} from '../status/statusVocabulary';

type ExecutionStatusPillClassProps = Readonly<{
  className: string;
  'data-state'?: string;
  'data-effect'?: 'shimmer-sweep';
  'data-shimmer-label'?: string;
}>;

function visibleStatusLabel(status: string | null | undefined): string {
  if (isWorkflowLifecycleStatus(status)) {
    return formatWorkflowStatusLabel(status, '-');
  }
  if (isStepLedgerStatus(status)) {
    return formatStepStatusLabel(status, '-');
  }
  if (isIntegrationStatus(status)) {
    return formatIntegrationStatusLabel(status, '-');
  }
  return formatWorkflowStatusLabel(status, '-');
}

function executionStatusPillProps(
  status: string | null | undefined,
  options: { enableMotion?: boolean } = {},
): ExecutionStatusPillClassProps {
  if (isWorkflowLifecycleStatus(status)) {
    return workflowStatusPillProps(status, options);
  }
  if (isStepLedgerStatus(status)) {
    return stepStatusPillProps(status);
  }
  if (isIntegrationStatus(status)) {
    return integrationStatusPillProps(status);
  }
  return workflowStatusPillProps(status, options);
}

function StatusPill({ label, pillProps }: { label: string; pillProps: ExecutionStatusPillClassProps }) {
  return (
    <span {...pillProps}>
      {pillProps['data-effect'] === 'shimmer-sweep' ? <span className="status-label">{label}</span> : label}
    </span>
  );
}

export function ExecutionStatusPill({
  status,
  enableMotion = true,
}: {
  status: string | null | undefined;
  enableMotion?: boolean;
}) {
  return (
    <StatusPill
      label={visibleStatusLabel(status)}
      pillProps={executionStatusPillProps(status, { enableMotion })}
    />
  );
}

type DomainStatusPillProps = WorkflowStatusPillOptions & {
  status: string | null | undefined;
};

export function WorkflowLifecycleStatusPill({
  status,
  enableMotion = true,
  completionDisposition,
  objectiveOutcome,
}: DomainStatusPillProps & {
  completionDisposition?: string | null | undefined;
  objectiveOutcome?: string | null | undefined;
}) {
  const override = workflowOutcomeOverride(status, completionDisposition, objectiveOutcome);
  if (override) {
    const { label, className } = workflowOutcomeOverridePresentation(override);
    return <StatusPill label={label} pillProps={{ className }} />;
  }
  return (
    <StatusPill
      label={formatWorkflowStatusLabel(status, '-')}
      pillProps={workflowStatusPillProps(status, { enableMotion })}
    />
  );
}

export function StepLedgerStatusPill({ status }: DomainStatusPillProps) {
  return <StatusPill label={formatStepStatusLabel(status, '-')} pillProps={stepStatusPillProps(status)} />;
}

export function StepExecutionStatusPill({ status }: DomainStatusPillProps) {
  return (
    <StatusPill
      label={formatStepExecutionStatusLabel(status, '-')}
      pillProps={stepExecutionStatusPillProps(status)}
    />
  );
}

export function IntegrationProviderStatusPill({ status }: DomainStatusPillProps) {
  return (
    <StatusPill
      label={formatIntegrationStatusLabel(status, '-')}
      pillProps={integrationStatusPillProps(status)}
    />
  );
}

function VocabularyStatusPill({ vocabulary, status }: { vocabulary: StatusVocabulary; status: string | null | undefined }) {
  return <StatusPill label={vocabulary.label(status)} pillProps={vocabulary.pillProps(status)} />;
}

export function CheckpointBranchStatusPill({ status }: { status: string | null | undefined }) {
  return <VocabularyStatusPill vocabulary={CHECKPOINT_BRANCH_STATUS} status={status} />;
}

export function CheckpointBranchTurnStatusPill({ status }: { status: string | null | undefined }) {
  return <VocabularyStatusPill vocabulary={CHECKPOINT_BRANCH_TURN_STATUS} status={status} />;
}

/**
 * A prerequisite or dependent row: the other workflow's lifecycle state, or the
 * dependency gate's outcome for it (terminated, timed out, bypassed, waiting
 * for a successful rerun).
 */
export function DependencyStatusPill({ status }: { status: string | null | undefined }) {
  if (isWorkflowLifecycleStatus(status)) {
    return <WorkflowLifecycleStatusPill status={status} />;
  }
  return <VocabularyStatusPill vocabulary={DEPENDENCY_OUTCOME_STATUS} status={status} />;
}
