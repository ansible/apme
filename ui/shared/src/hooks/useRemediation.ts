import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useApiClient } from './useApiClient';

export function useRemediationQueue(scanId?: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ['remediation-queue', scanId],
    queryFn: () => client.listRemediationQueue(scanId),
  });
}

export function useAcceptProposal() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, reviewedBy }: { proposalId: string; reviewedBy?: string }) =>
      client.acceptProposal(proposalId, reviewedBy),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['remediation-queue'] });
    },
  });
}

export function useRejectProposal() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, reviewedBy }: { proposalId: string; reviewedBy?: string }) =>
      client.rejectProposal(proposalId, reviewedBy),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['remediation-queue'] });
    },
  });
}
