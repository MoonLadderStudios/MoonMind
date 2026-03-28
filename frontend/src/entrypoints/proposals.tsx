import { DataTable } from '../components/tables/DataTable';
import { useQuery } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';

interface Proposal {
  id: string;
  title: string;
  status: string;
}

function ProposalsPage() {
  const { data, isLoading, isError, error } = useQuery<{ items: Proposal[] }>({
    queryKey: ['proposals'],
    queryFn: async () => {
      const response = await fetch('/api/proposals');
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      return response.json();
    },
  });

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <header className="border-b border-gray-200 pb-4 mb-6">
        <h2 className="text-2xl font-bold text-gray-900 tracking-tight">Proposals</h2>
        <p className="text-sm text-gray-500 mt-1">Review and manage task proposals.</p>
      </header>
      <div className="bg-white rounded-lg shadow-sm p-6 border border-gray-100">
        {isLoading ? (
          <p className="text-gray-500 italic animate-pulse">Loading proposals...</p>
        ) : isError ? (
          <div className="p-4 rounded-md bg-red-50 text-red-700 border border-red-200 mb-4">{(error as Error).message}</div>
        ) : (
          <DataTable
            data={data?.items || []}
            columns={[
              { key: 'id', header: 'ID' },
              { key: 'title', header: 'Title' },
              { key: 'status', header: 'Status' },
            ]}
            emptyMessage="No proposals found."
            getRowKey={(item) => item.id}
          />
        )}
      </div>
    </div>
  );
}

mountPage(ProposalsPage);
