import { useMutation, useQuery } from '@tanstack/react-query';
import { useApiClient } from './useApiClient';

export function useFixJob(jobId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: ['fix-job', jobId],
    queryFn: () => client.getFixJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data && (data.status === 'completed' || data.status === 'failed')) {
        return false;
      }
      return 1000;
    },
  });
}

export function useCreateFixJob() {
  const client = useApiClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof client.createFixJob>[0]) =>
      client.createFixJob(body),
  });
}
