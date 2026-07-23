import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import {
  Alert,
  AlertActionCloseButton,
  Button,
  Card,
  CardBody,
  ExpandableSection,
} from '@patternfly/react-core';
import JSZip from 'jszip';
import { AI_MODEL_STORAGE_KEY } from '@apme/ui-workflow';
import {
  CheckOptionsForm,
  getPersistedSession,
  OperationProgressPanel,
  OperationResultCard,
  ProposalReviewPanel,
  Tier1ResultsPanel,
  useSessionStream,
  type OperationProgress,
  type OperationProposal,
  type OperationResult,
  type OperationStatus,
  type Patch,
  type SessionResult,
  type Tier1Result,
} from '@apme/ui-workflow';
import { RuleId } from '../components/RuleId';
import { useFeedbackEnabled } from '../hooks/useFeedbackEnabled';

function mapSessionStatus(s: string): OperationStatus {
  if (s === 'uploading') return 'preparing';
  return s as OperationStatus;
}

export function PlaygroundPage() {
  const feedbackEnabled = useFeedbackEnabled();
  const [files, setFiles] = useState<File[]>([]);
  const [ansibleVersion, setAnsibleVersion] = useState('');
  const [collections, setCollections] = useState('');
  const [enableAi, setEnableAi] = useState(true);
  const [autoApplyTier1, setAutoApplyTier1] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);

  const {
    status: rawStatus,
    progress: rawProgress,
    sessionId,
    scanId,
    tier1,
    proposals: rawProposals,
    result,
    error,
    canReconnect,
    startSession,
    resumeSession,
    approve,
    cancel,
    reset,
  } = useSessionStream();

  // Auto-resume a persisted session when the page mounts.
  const resumeAttempted = useRef(false);
  useEffect(() => {
    if (resumeAttempted.current || rawStatus !== 'idle') return;
    resumeAttempted.current = true;
    const persisted = getPersistedSession();
    if (persisted) {
      resumeSession(persisted.sessionId, persisted.scanId);
    }
  }, [rawStatus, resumeSession]);

  const opStatus = mapSessionStatus(rawStatus);
  const opProgress: OperationProgress[] = rawProgress.map((p) => ({
    phase: p.phase,
    message: p.message,
    timestamp: p.timestamp,
  }));
  const opProposals: OperationProposal[] = rawProposals.map((p) => ({
    id: p.id,
    rule_id: p.rule_id,
    file: p.file,
    tier: p.tier,
    confidence: p.confidence,
    explanation: p.explanation,
    diff_hunk: p.diff_hunk,
    status: p.status,
    suggestion: p.suggestion,
    line_start: p.line_start,
    path: p.path,
    node_type: p.node_type,
    source: p.source,
    before_text: p.before_text,
    after_text: p.after_text,
  }));

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      if (rawStatus !== 'idle') return;
      const dropped = Array.from(e.dataTransfer.files);
      setFiles((prev) => [...prev, ...dropped]);
    },
    [rawStatus],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (!e.target.files) return;
      setFiles((prev) => [...prev, ...Array.from(e.target.files!)]);
      e.target.value = '';
    },
    [],
  );

  const removeFile = useCallback((idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const handleSubmit = useCallback(() => {
    if (files.length === 0) return;
    const colls = collections
      .split(',')
      .map((c) => c.trim())
      .filter(Boolean);
    startSession(files, {
      ansibleVersion,
      collections: colls.length ? colls : undefined,
      enableAi,
      aiModel: enableAi ? (localStorage.getItem(AI_MODEL_STORAGE_KEY) ?? undefined) : undefined,
    });
  }, [files, ansibleVersion, collections, enableAi, startSession]);

  const handleReset = useCallback(() => {
    reset();
    setFiles([]);
  }, [reset]);

  const isRunning = opStatus === 'connecting' || opStatus === 'preparing' || opStatus === 'checking' || opStatus === 'applying';
  const [dismissedDisconnect, setDismissedDisconnect] = useState(false);

  const handleReconnect = useCallback(() => {
    if (sessionId && scanId) {
      resumeSession(sessionId, scanId);
    }
  }, [sessionId, scanId, resumeSession]);

  return (
    <PageLayout>
      <PageHeader
        title="Playground"
        description="Ad-hoc check — upload files directly for a quick lint check. Results are not persisted to any project."
      />

      <div style={{ padding: '0 24px 24px' }}>
        {rawStatus === 'disconnected' && canReconnect && !dismissedDisconnect && (
          <Alert
            variant="warning"
            isInline
            title="Session disconnected"
            actionClose={<AlertActionCloseButton onClose={() => setDismissedDisconnect(true)} />}
            style={{ marginBottom: 16 }}
          >
            <p style={{ marginBottom: 8 }}>{error}</p>
            <Button variant="primary" onClick={handleReconnect} size="sm">
              Reconnect
            </Button>
            {' '}
            <Button variant="link" onClick={handleReset} size="sm">
              Start Over
            </Button>
          </Alert>
        )}

        {opStatus === 'idle' && (
          <Card>
            <CardBody>
              <div
                className={`apme-drop-zone ${isDragOver ? 'drag-over' : ''}`}
                onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
              >
                <div className="apme-drop-icon">+</div>
                <div className="apme-drop-text">
                  Drop Ansible files here or click to browse
                </div>
                <div className="apme-drop-hint">
                  Supports individual files or entire directories
                </div>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".yml,.yaml,.json,.j2,.jinja2,.cfg,.ini,.toml,.py,.sh"
                  style={{ display: 'none' }}
                  onChange={handleFileSelect}
                />
              </div>

              <div style={{ marginTop: 8 }}>
                <Button variant="secondary" onClick={() => dirInputRef.current?.click()}>
                  Select Directory
                </Button>
                <input
                  ref={dirInputRef}
                  type="file"
                  /* @ts-expect-error webkitdirectory is non-standard */
                  webkitdirectory=""
                  style={{ display: 'none' }}
                  onChange={handleFileSelect}
                />
              </div>

              {files.length > 0 && (
                <div className="apme-file-list">
                  <h3>
                    {files.length} file{files.length !== 1 ? 's' : ''} selected
                  </h3>
                  <ul>
                    {files.map((f, i) => (
                      <li key={`${f.name}-${i}`} className="apme-file-item">
                        <span className="apme-file-name">
                          {(f as File & { webkitRelativePath?: string })
                            .webkitRelativePath || f.name}
                        </span>
                        <span className="apme-file-size">
                          {(f.size / 1024).toFixed(1)} KB
                        </span>
                        <Button variant="plain" onClick={() => removeFile(i)} aria-label={`Remove ${f.name}`} size="sm">
                          &times;
                        </Button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <CheckOptionsForm
                ansibleVersion={ansibleVersion}
                onAnsibleVersionChange={setAnsibleVersion}
                collections={collections}
                onCollectionsChange={setCollections}
                enableAi={enableAi}
                onEnableAiChange={setEnableAi}
                autoApplyTier1={autoApplyTier1}
                onAutoApplyTier1Change={setAutoApplyTier1}
              />

              <Button
                variant="primary"
                isDisabled={files.length === 0}
                onClick={handleSubmit}
                style={{ marginTop: 16 }}
              >
                Start Check
              </Button>
            </CardBody>
          </Card>
        )}

        {isRunning && (
          <OperationProgressPanel status={opStatus} progress={opProgress} onCancel={cancel} />
        )}

        {rawStatus === 'tier1_done' && tier1 && (
          <Tier1ResultsPanel tier1={tier1} />
        )}

        {rawStatus === 'awaiting_approval' && opProposals.length > 0 && (
          <>
            {tier1 && <Tier1ResultsPanel tier1={tier1} />}
            <ProposalReviewPanel proposals={opProposals} onApprove={approve} feedbackEnabled={feedbackEnabled} scanId={scanId ?? undefined} />
          </>
        )}

        {rawStatus === 'complete' && result && (
          <SessionComplete result={result} scanId={scanId} tier1={tier1} onReset={handleReset} />
        )}
        {rawStatus === 'complete' && !result && (
          <Card style={{ textAlign: 'center', padding: 48 }}>
            <CardBody>
              <div style={{ fontSize: 48, color: 'var(--pf-t--global--color--status--success--default)' }}>&#10003;</div>
              <h2>Check Complete</h2>
              <Button variant="primary" onClick={handleReset} style={{ marginTop: 16 }}>
                Check More Files
              </Button>
            </CardBody>
          </Card>
        )}

        {rawStatus === 'error' && (
          <Card style={{ textAlign: 'center', padding: 48 }}>
            <CardBody>
              <h2 style={{ color: 'var(--pf-t--global--color--status--danger--default)' }}>Check Failed</h2>
              <p style={{ opacity: 0.7 }}>{error}</p>
              <Button variant="primary" onClick={handleReset}>
                Try Again
              </Button>
            </CardBody>
          </Card>
        )}
      </div>
    </PageLayout>
  );
}

function SessionComplete({
  result,
  scanId,
  tier1,
  onReset,
}: {
  result: SessionResult;
  scanId: string | null;
  tier1: Tier1Result | null;
  onReset: () => void;
}) {
  const totalPatches = result.patches.length + (tier1?.patches.length ?? 0);
  const remaining = result.remaining_violations.length;

  const patchedFiles = useMemo(() => {
    const byPath = new Map<string, Patch>();
    for (const p of tier1?.patches ?? []) {
      if (p.patched) byPath.set(p.file, p);
    }
    for (const p of result.patches) {
      if (p.patched) byPath.set(p.file, p);
    }
    return byPath;
  }, [tier1, result]);

  const [downloading, setDownloading] = useState(false);

  const handleDownload = useCallback(async () => {
    if (patchedFiles.size === 0) return;
    setDownloading(true);
    try {
      const zip = new JSZip();
      for (const [path, patch] of patchedFiles) {
        const bytes = Uint8Array.from(atob(patch.patched!), (c) => c.charCodeAt(0));
        zip.file(path, bytes);
      }
      const blob = await zip.generateAsync({ type: 'blob' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `apme-remediated-${scanId ?? 'files'}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }, [patchedFiles, scanId]);

  const opResult: OperationResult = {
    total_violations: remaining,
    fixable: 0,
    ai_candidate: 0,
    ai_proposed: 0,
    ai_declined: 0,
    ai_accepted: 0,
    manual_review: remaining,
    remediated_count: totalPatches,
  };

  return (
    <OperationResultCard
      result={opResult}
      onDismiss={onReset}
      actions={
        <>
          {patchedFiles.size > 0 && (
            <Button variant="primary" onClick={handleDownload} isDisabled={downloading}>
              {downloading ? 'Preparing...' : `Download Remediated Files (${patchedFiles.size})`}
            </Button>
          )}
          {remaining > 0 && (
            <ExpandableSection toggleText={`Remaining Violations (${remaining})`} style={{ textAlign: 'left', maxWidth: 700, margin: '8px auto 0' }}>
              <div className="apme-remaining-list">
                {result.remaining_violations.map((v, i) => (
                  <div key={i} className="apme-remaining-item">
                    <RuleId ruleId={v.rule_id} />
                    <span className="apme-file-name">{v.file}</span>
                    <span>{v.message}</span>
                  </div>
                ))}
              </div>
            </ExpandableSection>
          )}
        </>
      }
    />
  );
}
