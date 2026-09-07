// Workflow Detail governance-report link helpers (MoonMind#3969).
// Presentation only: the authoritative report JSON, status, and explanations
// are owned by moonmind/governance/run_reports.py. These helpers map that
// server-provided status to hrefs and safe fallback copy without inventing
// new report semantics.

export const GOVERNANCE_REPORT_STATUSES = ['ready', 'partial', 'pending', 'failed'] as const;

export type GovernanceReportStatus = (typeof GOVERNANCE_REPORT_STATUSES)[number];

const GOVERNANCE_STATUS_FALLBACK_COPY: Record<GovernanceReportStatus, string> = {
  ready: 'Governance report is ready from verified authoritative evidence.',
  partial: 'Governance report is partial: some evidence was unavailable and is shown as such.',
  pending: 'Governance report is pending: evidence collection has not completed.',
  failed:
    'Report generation failed as an auxiliary step; task and publication state are unchanged.',
};

export function isGovernanceReportStatus(value: unknown): value is GovernanceReportStatus {
  return (
    typeof value === 'string' &&
    (GOVERNANCE_REPORT_STATUSES as readonly string[]).includes(value)
  );
}

export function normalizeGovernanceReportStatus(value: unknown): GovernanceReportStatus {
  return isGovernanceReportStatus(value) ? value : 'pending';
}

export function governanceReportHref(workflowId: string, reportId?: string | null): string {
  const safeId = encodeURIComponent(workflowId);
  const report = typeof reportId === 'string' && reportId.trim() ? `?report=${encodeURIComponent(reportId.trim())}` : '';
  return `/workflows/${safeId}/evidence${report}`;
}

export function governanceReportExplanation(status: unknown, serverExplanation?: unknown): string {
  if (typeof serverExplanation === 'string' && serverExplanation.trim()) {
    return serverExplanation;
  }
  return GOVERNANCE_STATUS_FALLBACK_COPY[normalizeGovernanceReportStatus(status)];
}
