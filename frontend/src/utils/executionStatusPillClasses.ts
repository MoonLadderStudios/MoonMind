import { formatIntegrationStatusLabel, integrationStatusPillProps } from '../status/integrationStatus';
import { formatStepStatusLabel, stepStatusPillProps } from '../status/stepStatus';
import {
  WORKFLOW_STATUS_KEYS,
  WORKFLOW_STATUS_TRACEABILITY,
  formatWorkflowStatusLabel,
  workflowStatusPillProps,
} from '../status/workflowStatus';

export const EXECUTING_STATUS_PILL_TRACEABILITY = WORKFLOW_STATUS_TRACEABILITY;
export const WORKFLOW_LIFECYCLE_STATUSES = WORKFLOW_STATUS_KEYS;

export type {
  WorkflowStatusPillOptions as ExecutionStatusPillOptions,
  WorkflowStatusPillProps as ExecutionStatusPillProps,
} from '../status/workflowStatus';

export { workflowStatusPillProps as executionStatusPillProps };

export type StatusPillView = Readonly<{
  label: string;
  pillProps: Readonly<{
    className: string;
    'data-state'?: string;
    'data-effect'?: 'shimmer-sweep';
    'data-shimmer-label'?: string;
  }>;
}>;

export function workflowLifecycleStatusPillView(
  status: string | null | undefined,
  options: { enableMotion?: boolean } = {},
): StatusPillView {
  return {
    label: formatWorkflowStatusLabel(status, 'Unknown'),
    pillProps: workflowStatusPillProps(status, options),
  };
}

export function stepLedgerStatusPillView(status: string | null | undefined): StatusPillView {
  return {
    label: formatStepStatusLabel(status, 'Unknown'),
    pillProps: stepStatusPillProps(status),
  };
}

export function integrationProviderStatusPillView(status: string | null | undefined): StatusPillView {
  return {
    label: formatIntegrationStatusLabel(status, 'Unknown'),
    pillProps: integrationStatusPillProps(status),
  };
}
