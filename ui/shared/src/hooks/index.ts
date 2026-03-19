/** Shared TanStack Query hooks for the APME API. */

export { useScans, useScan, useCreateScan, useDeleteScan } from './useScans';
export { useRules, useRule } from './useRules';
export { useHealth } from './useHealth';
export {
  useRemediationQueue,
  useAcceptProposal,
  useRejectProposal,
} from './useRemediation';
export { useFixJob, useCreateFixJob } from './useFix';
