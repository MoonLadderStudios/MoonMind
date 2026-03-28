import { DataTable } from '../components/tables/DataTable';
import { useQuery } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';

interface TaskRun {
  taskId: string;
  source: string;
  sourceLabel: string;
  status: string;
}

function TasksListPage() {
  const { data, isLoading, isError, error } = useQuery<{ items: TaskRun[] }>({
    queryKey: ['tasks-list'],
    queryFn: async () => {
      const response = await fetch('/api/executions');
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      return response.json();
    },
  });

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <header className="border-b border-gray-200 pb-4 mb-6">
        <h2 className="text-2xl font-bold text-gray-900 tracking-tight">Tasks List</h2>
        <p className="text-sm text-gray-500 mt-1">Unified tasks across available execution sources.</p>
      </header>
      <div className="bg-white rounded-lg shadow-sm p-6 border border-gray-100">
        {isLoading ? (
          <p className="text-gray-500 italic animate-pulse">Loading tasks...</p>
        ) : isError ? (
          <div className="p-4 rounded-md bg-red-50 text-red-700 border border-red-200 mb-4">{(error as Error).message}</div>
        ) : (
          <DataTable
            data={data?.items || []}
            columns={[
              { key: 'taskId', header: 'Task ID', render: (item: TaskRun) => <a href={`/tasks/${item.taskId}`} className="text-blue-600 hover:underline">{item.taskId}</a> },
              { key: 'sourceLabel', header: 'Source', render: (item: TaskRun) => item.sourceLabel || item.source },
              { key: 'status', header: 'Status' },
            ]}
            emptyMessage="No tasks found."
          />
        )}
      </div>
    </div>
  );
}

mountPage(TasksListPage);
