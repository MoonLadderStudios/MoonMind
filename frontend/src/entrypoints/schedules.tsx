import { useQuery } from '@tanstack/react-query';
import { DataTable } from '../components/tables/DataTable';

import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';

const ScheduleSchema = z.object({
  id: z.string(),
  name: z.string(),
  enabled: z.boolean().optional(),
  cron: z.string().optional(),
  timezone: z.string().optional(),
  lastDispatchStatus: z.string().nullable().optional(),
  nextRunAt: z.string().nullable().optional(),
});

const SchedulesResponseSchema = z.object({
  items: z.array(ScheduleSchema),
});

export function SchedulesPage({ payload }: { payload: BootPayload }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['schedules'],
    queryFn: async () => {
      const response = await fetch(`${payload.apiBase}/recurring-tasks?scope=personal`);
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      return SchedulesResponseSchema.parse(await response.json());
    },
  });

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <header className="border-b border-gray-200 pb-4 mb-6 flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 tracking-tight">Recurring Schedules</h2>
          <p className="text-sm text-gray-500 mt-1">Managed recurring schedules for queue and manifest targets.</p>
        </div>
        <a href="/workflows/new?scheduleMode=recurring" className="text-blue-600 hover:underline">
          Create from workflow page
        </a>
      </header>
      <div className="bg-white rounded-lg shadow-sm p-6 border border-gray-100">
        {isLoading ? (
          <p className="text-gray-500 italic animate-pulse">Loading recurring schedules...</p>
        ) : isError ? (
          <div className="p-4 rounded-md bg-red-50 text-red-700 border border-red-200 mb-4">{(error as Error).message}</div>
        ) : (
          <DataTable
            data={data?.items || []}
            columns={[
              { key: 'id', header: 'ID' },
              { key: 'name', header: 'Name' },
              {
                key: 'lastDispatchStatus',
                header: 'Last Dispatch Status',
                render: (item) => item.lastDispatchStatus ?? '—',
              },
              {
                key: 'nextRunAt',
                header: 'Next Run At',
                render: (item) => item.nextRunAt ? new Date(item.nextRunAt).toLocaleString() : '—',
              },
            ]}
            emptyMessage="No recurring schedules found."
            getRowKey={(item) => item.id}
          />
        )}
      </div>
    </div>
  );
}
export default SchedulesPage;
