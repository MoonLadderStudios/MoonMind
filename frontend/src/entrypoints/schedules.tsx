import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';
import { DataTable } from '../components/tables/DataTable';

import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';

const ScheduleSchema = z.object({
  id: z.string(),
  name: z.string(),
  lastDispatchStatus: z.string(),
  nextRunAt: z.string(),
  schedule: z.string().optional(),
});
type Schedule = z.infer<typeof ScheduleSchema>;

interface SaveSchedulePayload {
  name: string;
  schedule: string;
}

const SchedulesResponseSchema = z.object({
  items: z.array(ScheduleSchema),
});

function SchedulesPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);

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

  const saveMutation = useMutation({
    mutationFn: async (mutationPayload: SaveSchedulePayload) => {
      const method = editingSchedule ? 'PUT' : 'POST';
      const endpoint = editingSchedule ? `${payload.apiBase}/recurring-tasks/${editingSchedule.id}` : `${payload.apiBase}/recurring-tasks`;
      const response = await fetch(endpoint, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mutationPayload),
      });
      if (!response.ok) {
        throw new Error(`Failed to save: ${response.statusText}`);
      }
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      setShowModal(false);
      setEditingSchedule(null);
    }
  });

  const handleEdit = (schedule: Schedule) => {
    setEditingSchedule(schedule);
    setShowModal(true);
  };

  const handleCreate = () => {
    setEditingSchedule(null);
    setShowModal(true);
  };

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <header className="border-b border-gray-200 pb-4 mb-6 flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 tracking-tight">Recurring Schedules</h2>
          <p className="text-sm text-gray-500 mt-1">Managed recurring schedules for queue and manifest targets.</p>
        </div>
        <button onClick={handleCreate} className="bg-blue-600 text-white px-4 py-2 rounded shadow hover:bg-blue-700">
          Create Schedule
        </button>
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
              { key: 'lastDispatchStatus', header: 'Last Dispatch Status' },
              { key: 'nextRunAt', header: 'Next Run At' },
              { key: 'actions', header: 'Actions', render: (item) => <button onClick={() => handleEdit(item)} className="text-blue-600 hover:underline">Edit</button> },
            ]}
            emptyMessage="No recurring schedules found."
            getRowKey={(item) => item.id}
          />
        )}
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex justify-center items-center p-4">
          <div className="bg-white p-6 rounded shadow-lg max-w-md w-full">
            <h3 className="text-lg font-bold mb-4">{editingSchedule ? 'Edit Schedule' : 'Create Schedule'}</h3>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const formData = new FormData(e.currentTarget);
                saveMutation.mutate({
                  name: String(formData.get('name') || ''),
                  schedule: String(formData.get('schedule') || ''),
                });
              }}
              className="space-y-4"
            >
              <div>
                <label className="block text-sm font-medium text-gray-700">Name</label>
                <input required type="text" name="name" defaultValue={editingSchedule?.name || ''} className="mt-1 block w-full rounded-md border-gray-300 shadow-sm" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Schedule (Cron)</label>
                <input required type="text" name="schedule" defaultValue={editingSchedule?.schedule || ''} className="mt-1 block w-full rounded-md border-gray-300 shadow-sm" placeholder="* * * * *" />
              </div>
              <div className="flex justify-end space-x-2 pt-4">
                <button type="button" onClick={() => setShowModal(false)} className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded">Cancel</button>
                <button type="submit" disabled={saveMutation.isPending} className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700">
                  {saveMutation.isPending ? 'Saving...' : 'Save'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

mountPage(SchedulesPage);
