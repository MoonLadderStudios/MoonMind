// minimal example hook
import { useQuery } from '@tanstack/react-query';
import { fetchApi } from '../api/client';
import { queryKeys } from './keys';

// Type aligned with the backend's UserProfileReadSanitized response.
// If OpenAPI-generated types are available, prefer importing that type here.
interface UserProfileReadSanitized {
  id: string;
  user_id: string;
  // Additional boolean flags may be present on the object.
  // They are omitted here for simplicity but can be added as needed.
  [key: string]: string | boolean;
}

export function useProfile() {
  return useQuery({
    queryKey: queryKeys.settings.profile,
    queryFn: () => fetchApi<UserProfileReadSanitized>('/api/me'),
  });
}
