import { useQuery } from '@tanstack/react-query';
import { useApiClient } from './useApiClient';

export function useRules(params?: { validator?: string; has_fixer?: boolean }) {
  const client = useApiClient();
  return useQuery({
    queryKey: ['rules', params],
    queryFn: () => client.listRules(params),
    staleTime: 5 * 60 * 1000,
  });
}

export function useRule(ruleId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: ['rule', ruleId],
    queryFn: () => client.getRule(ruleId!),
    enabled: !!ruleId,
    staleTime: 5 * 60 * 1000,
  });
}
