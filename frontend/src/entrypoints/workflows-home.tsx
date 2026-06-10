import { useQuery } from '@tanstack/react-query';

import { BootPayload } from '../boot/parseBootPayload';
import type { components } from '../generated/openapi';

type ExecutionMetrics = components['schemas']['ExecutionMetricsResponse'];

const METRICS_SAMPLE_SIZE = 500;

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—';
  }
  return new Intl.NumberFormat('en-US', {
    style: 'percent',
    maximumFractionDigits: 1,
  }).format(value);
}

function formatDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || Number.isNaN(seconds) || seconds < 0) {
    return '—';
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }

  const roundedSeconds = Math.round(seconds);
  const hours = Math.floor(roundedSeconds / 3600);
  const minutes = Math.floor((roundedSeconds % 3600) / 60);
  const remainingSeconds = roundedSeconds % 60;

  if (hours > 0) {
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  return remainingSeconds > 0 ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
}

function formatUsd(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—';
  }
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: value >= 10 ? 2 : 4,
  }).format(value);
}

function buildMetricsUrl(apiBase: string): string {
  const params = new URLSearchParams({
    scope: 'tasks',
    sampleSize: String(METRICS_SAMPLE_SIZE),
  });
  return `${apiBase}/executions/metrics?${params.toString()}`;
}

function MetricTile({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="card min-h-32">
      <dt className="text-sm font-semibold text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="mt-3 text-3xl font-semibold tabular-nums text-slate-950 dark:text-slate-50">
        {value}
      </dd>
      <dd className="mt-2 text-sm text-slate-500 dark:text-slate-400">{detail}</dd>
    </div>
  );
}

export function WorkflowsHomePage({ payload }: { payload: BootPayload }) {
  const apiBase = payload.apiBase || '/api';
  const metricsQuery = useQuery({
    queryKey: ['workflow-home', 'execution-metrics', apiBase],
    queryFn: async (): Promise<ExecutionMetrics> => {
      const response = await fetch(buildMetricsUrl(apiBase), { credentials: 'include' });
      if (!response.ok) {
        throw new Error(`Failed to fetch operational metrics: ${response.statusText}`);
      }
      return (await response.json()) as ExecutionMetrics;
    },
  });

  const metrics = metricsQuery.data;
  const duration = metrics?.duration;
  const cost = metrics?.cost;
  const refreshedAt = metrics?.refreshedAt
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      }).format(new Date(metrics.refreshedAt))
    : '—';

  return (
    <main className="space-y-6" aria-label="Mission Control dashboard">
      <header>
        <p className="eyebrow">Mission Control</p>
        <h1>Operational Metrics</h1>
        <p className="subhead">
          Run duration, success rate, and cost across recent task executions.
        </p>
      </header>

      <section aria-label="Operational metrics" className="space-y-4">
        {metricsQuery.isLoading ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">Loading operational metrics...</p>
        ) : null}

        {metricsQuery.isError ? (
          <p className="text-sm text-rose-600 dark:text-rose-300">
            Operational metrics are unavailable.
          </p>
        ) : null}

        {!metricsQuery.isLoading && !metricsQuery.isError && metrics ? (
          <>
            <dl className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <MetricTile
                label="Runs"
                value={String(metrics.totalRuns ?? '—')}
                detail={`${metrics.terminalRuns ?? 0} terminal from ${metrics.sampleSize ?? 0} sampled`}
              />
              <MetricTile
                label="Success Rate"
                value={formatPercent(metrics.successRate)}
                detail={`${metrics.completedRuns ?? 0} completed, ${metrics.failedRuns ?? 0} failed`}
              />
              <MetricTile
                label="Run Duration"
                value={formatDuration(duration?.averageSeconds)}
                detail={`Median ${formatDuration(duration?.medianSeconds)} across ${duration?.observedCount ?? 0} runs`}
              />
              <MetricTile
                label="Cost"
                value={formatUsd(cost?.totalEstimateUsd)}
                detail={`Average ${formatUsd(cost?.averageEstimateUsd)} across ${cost?.observedCount ?? 0} runs`}
              />
            </dl>

            <p className="text-xs text-slate-500 dark:text-slate-400">
              Refreshed {refreshedAt}
              {metrics.countMode === 'estimated_or_unknown' ? ' · count estimate' : ''}
            </p>
          </>
        ) : null}
      </section>
    </main>
  );
}

export default WorkflowsHomePage;
