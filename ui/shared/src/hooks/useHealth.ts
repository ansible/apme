import { useQuery } from '@tanstack/react-query';
import { useApiClient } from './useApiClient';

export function useHealth() {
  const client = useApiClient();
  return useQuery({
    queryKey: ['health'],
    queryFn: () => client.getHealth(),
    refetchInterval: 30_000,
  });
}
