import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ScanCreate } from '../api-client/models';
import { useApiClient } from './useApiClient';

export function useScans(params?: {
  page?: number;
  page_size?: number;
  status_filter?: string;
  project?: string;
}) {
  const client = useApiClient();
  return useQuery({
    queryKey: ['scans', params],
    queryFn: () => client.listScans(params),
  });
}

export function useScan(scanId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: ['scan', scanId],
    queryFn: () => client.getScan(scanId!),
    enabled: !!scanId,
  });
}

export function useCreateScan() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ScanCreate) => client.createScan(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scans'] });
    },
  });
}

export function useDeleteScan() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scanId: string) => client.deleteScan(scanId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scans'] });
    },
  });
}
