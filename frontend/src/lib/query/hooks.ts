// minimal example hook
import { useQuery } from '@tanstack/react-query';
import { fetchApi } from '../api/client';
import { queryKeys } from './keys';

// Example type, replace with OpenAPI generated type if available
interface ProfileResponse {
  id: string;
  user_id: string;
  [key: string]: string | boolean;
}

export function useProfile() {
  return useQuery({
    queryKey: queryKeys.settings.profile,
    queryFn: () => fetchApi<ProfileResponse>('/api/me'),
  });
}
