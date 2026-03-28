import { useQuery } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';
import { BootPayload } from '../boot/parseBootPayload';

interface TaskRunDetail {
  taskId: string;
  source: string;
  sourceLabel: string;
  status: string;
  createdAt: string;
  temporalRunId?: string;
  namespace?: string;
  executionEnvironment?: string;
}

function TaskDetailPage(_props: { payload: BootPayload }) {
  const taskIdMatch = window.location.pathname.match(/^\/tasks\/(?:temporal\/|proposals\/|schedules\/|manifests\/)?([^/]+)$/);
  const taskId = taskIdMatch ? taskIdMatch[1] : null;

  const { data, isLoading, isError, error } = useQuery<TaskRunDetail>({
    queryKey: ['task-detail', taskId],
    queryFn: async () => {
      const response = await fetch(`/api/executions/${taskId}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch task ${taskId}: ${response.statusText}`);
      }
      return response.json();
    },
    enabled: !!taskId,
  });

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <header className="border-b border-gray-200 pb-4 mb-6">
        <h2 className="text-2xl font-bold text-gray-900 tracking-tight">Task Detail</h2>
        <p className="text-sm text-gray-500 mt-1">Task {taskId}</p>
      </header>
      <div className="bg-white rounded-lg shadow-sm p-6 border border-gray-100">
        {isLoading ? (
          <p className="text-gray-500 italic animate-pulse">Loading task details...</p>
        ) : isError ? (
          <div className="p-4 rounded-md bg-red-50 text-red-700 border border-red-200 mb-4">{(error as Error).message}</div>
        ) : data ? (
          <div className="overflow-x-auto w-full rounded shadow-sm border border-gray-200">
            <table className="min-w-full text-left text-sm whitespace-nowrap">
              <tbody className="divide-y divide-gray-200">
                <tr className="hover:bg-gray-50 transition-colors"><th className="px-4 py-3 font-medium text-gray-900 bg-gray-50 w-1/4">Task ID</th><td className="px-4 py-3">{data.taskId}</td></tr>
                <tr className="hover:bg-gray-50 transition-colors"><th className="px-4 py-3 font-medium text-gray-900 bg-gray-50 w-1/4">Status</th><td className="px-4 py-3">{data.status}</td></tr>
                <tr className="hover:bg-gray-50 transition-colors"><th className="px-4 py-3 font-medium text-gray-900 bg-gray-50 w-1/4">Source</th><td className="px-4 py-3">{data.sourceLabel || data.source}</td></tr>
                <tr className="hover:bg-gray-50 transition-colors"><th className="px-4 py-3 font-medium text-gray-900 bg-gray-50 w-1/4">Created At</th><td className="px-4 py-3">{data.createdAt}</td></tr>
                {data.temporalRunId && <tr className="hover:bg-gray-50 transition-colors"><th className="px-4 py-3 font-medium text-gray-900 bg-gray-50 w-1/4">Temporal Run ID</th><td className="px-4 py-3">{data.temporalRunId}</td></tr>}
                {data.namespace && <tr className="hover:bg-gray-50 transition-colors"><th className="px-4 py-3 font-medium text-gray-900 bg-gray-50 w-1/4">Namespace</th><td className="px-4 py-3">{data.namespace}</td></tr>}
                {data.executionEnvironment && <tr className="hover:bg-gray-50 transition-colors"><th className="px-4 py-3 font-medium text-gray-900 bg-gray-50 w-1/4">Environment</th><td className="px-4 py-3">{data.executionEnvironment}</td></tr>}
              </tbody>
            </table>
          </div>
        ) : (
          <p>No task details found.</p>
        )}
      </div>
    </div>
  );
}

mountPage(TaskDetailPage);
