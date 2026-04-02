/**
 * WebSocket hook for project check/remediate operations (ADR-037).
 *
 * Connects to WS /api/v1/projects/{id}/ws/operate and manages
 * the clone → check/remediate → result lifecycle with progress streaming.
 */

import { useCallback, useRef, useState } from "react";

export type ProjectOperationStatus =
  | "idle"
  | "connecting"
  | "cloning"
  | "checking"
  | "awaiting_approval"
  | "applying"
  | "complete"
  | "error";

export interface ProgressEntry {
  phase: string;
  message: string;
  timestamp: number;
  progress?: number;
  level?: number;
}

export interface ProjectProposal {
  id: string;
  rule_id: string;
  file: string;
  tier: number;
  confidence: number;
  explanation?: string;
  diff_hunk?: string;
  status?: 'proposed' | 'declined';
  suggestion?: string;
  line_start?: number;
}

export interface ProjectOperationResult {
  total_violations: number;
  fixable: number;
  ai_candidate: number;
  ai_proposed: number;
  ai_declined: number;
  ai_accepted: number;
  manual_review: number;
  remediated_count?: number;
}

export interface ProjectOperationOptions {
  remediate?: boolean;
  ansible_version?: string;
  collection_specs?: string[];
  enable_ai?: boolean;
  ai_model?: string;
}

function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

export function useProjectOperation(projectId: string) {
  const [status, setStatus] = useState<ProjectOperationStatus>("idle");
  const [progress, setProgress] = useState<ProgressEntry[]>([]);
  const [scanId, setScanId] = useState<string | null>(null);
  const [proposals, setProposals] = useState<ProjectProposal[]>([]);
  const [result, setResult] = useState<ProjectOperationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRemediate, setIsRemediate] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const statusRef = useRef<ProjectOperationStatus>(status);
  statusRef.current = status;

  const reset = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus("idle");
    setProgress([]);
    setScanId(null);
    setProposals([]);
    setResult(null);
    setError(null);
    setIsRemediate(false);
  }, []);

  const startOperation = useCallback(
    (options: ProjectOperationOptions = {}) => {
      reset();
      setIsRemediate(options.remediate ?? false);
      setStatus("connecting");

      const ws = new WebSocket(
        wsUrl(`/api/v1/projects/${projectId}/ws/operate`),
      );
      wsRef.current = ws;

      ws.onopen = () => {
        ws.send(
          JSON.stringify({
            type: "start",
            remediate: options.remediate ?? false,
            options: {
              ansible_version: options.ansible_version || "",
              collection_specs: options.collection_specs || [],
              enable_ai: options.enable_ai ?? false,
              ai_model: options.ai_model || "",
            },
          }),
        );
      };

      ws.onmessage = (event) => {
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(event.data as string);
        } catch {
          return;
        }

        switch (msg.type) {
          case "cloning":
            setStatus("cloning");
            break;

          case "started":
            setScanId(msg.scan_id as string);
            setStatus("checking");
            break;

          case "progress":
            setProgress((prev) => [
              ...prev,
              {
                phase: (msg.phase as string) || "",
                message: (msg.message as string) || "",
                timestamp: Date.now(),
                progress: typeof msg.progress === "number" ? msg.progress : undefined,
                level: typeof msg.level === "number" ? msg.level : undefined,
              },
            ]);
            break;

          case "proposals":
            setProposals(msg.proposals as ProjectProposal[]);
            setStatus("awaiting_approval");
            break;

          case "approval_ack":
            setStatus("applying");
            break;

          case "result":
            setResult(msg as unknown as ProjectOperationResult);
            setStatus("complete");
            break;

          case "error":
            setError((msg.message as string) || "Unknown error");
            setStatus("error");
            break;

          case "closed":
            if (statusRef.current !== "complete" && statusRef.current !== "error") {
              setStatus("complete");
            }
            break;
        }
      };

      ws.onerror = () => {
        setError("WebSocket connection error");
        setStatus("error");
      };

      ws.onclose = (event) => {
        if (event.code !== 1000 && statusRef.current !== "complete" && statusRef.current !== "error") {
          setError("Connection closed unexpectedly");
          setStatus("error");
        }
      };
    },
    [projectId, reset],
  );

  const approve = useCallback(
    (approvedIds: string[]) => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({ type: "approve", approved_ids: approvedIds }),
        );
      }
    },
    [],
  );

  const cancel = useCallback(() => {
    wsRef.current?.close();
    setStatus("idle");
  }, []);

  return {
    status,
    progress,
    scanId,
    proposals,
    result,
    error,
    isRemediate,
    startOperation,
    approve,
    cancel,
    reset,
  };
}
