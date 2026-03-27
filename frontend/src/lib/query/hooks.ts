// minimal example hook
import { useQuery } from '@tanstack/react-query';
import { fetchApi } from '../api/client';
import { queryKeys } from './keys';

// Example type, replace with OpenAPI generated type if available
interface ProfileResponse {
  email: string;
  is_active: boolean;
  is_superuser: boolean;
}

export function useProfile() {
  return useQuery({
    queryKey: queryKeys.settings.profile,
    queryFn: () => fetchApi<ProfileResponse>('/api/me/profile'),
  });
}
