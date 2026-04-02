import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import { severityClass, severityLabel, severityOrder, SEVERITY_LABELS, bareRuleId, healthColor } from '../components/severity';
import {
  Button,
  Card,
  CardBody,
  Flex,
  FlexItem,
  Split,
  SplitItem,
  Tab,
  Tabs,
  TabTitleText,
  TextInput,
} from '@patternfly/react-core';
import { deleteProject, getProject, getProjectDependencies, getProjectGraph, listProjectActivity, listProjectViolations, updateProject } from '../services/api';
import type { GraphData } from '../services/api';
import type { ActivitySummary, ProjectDependencies, ProjectDetail, ViolationDetail } from '../types/api';
import { GraphVisualization } from '../components/GraphVisualization';
import type { OperationStatus, OperationProgress, OperationProposal, OperationResult } from '../types/operation';
import { StatusBadge } from '../components/StatusBadge';
import { CheckOptionsForm } from '../components/CheckOptionsForm';
import { OperationProgressPanel } from '../components/OperationProgressPanel';
import { ProposalReviewPanel } from '../components/ProposalReviewPanel';
import { OperationResultCard } from '../components/OperationResultCard';
import { timeAgo } from '../services/format';
import { useFeedbackEnabled } from '../hooks/useFeedbackEnabled';
import { useProjectOperation, type ProjectOperationOptions } from '../hooks/useProjectOperation';
import { AI_MODEL_STORAGE_KEY } from './SettingsPage';

function mapProjectStatus(s: string): OperationStatus {
  return s as OperationStatus;
}

export function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [scans, setScans] = useState<ActivitySummary[]>([]);
  const [violations, setViolations] = useState<ViolationDetail[]>([]);
  const [dependencies, setDependencies] = useState<ProjectDependencies | null>(null);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const tabParam = searchParams.get('tab');
  const [activeTab, setActiveTab] = useState(
    tabParam === 'settings' ? 5 : tabParam === 'visualize' ? 4 : tabParam === 'dependencies' ? 3 : 0
  );

  const [ansibleVersion, setAnsibleVersion] = useState('');
  const [collections, setCollections] = useState('');
  const [enableAi, setEnableAi] = useState(true);

  const feedbackEnabled = useFeedbackEnabled();
  const {
    status: rawStatus,
    progress: rawProgress,
    scanId: opScanId,
    proposals: rawProposals,
    result: rawResult,
    error: opError,
    startOperation,
    approve: opApprove,
    cancel: opCancel,
    reset: opReset,
  } = useProjectOperation(projectId || '');

  const opStatus = mapProjectStatus(rawStatus);
  const opProgress: OperationProgress[] = rawProgress.map((p) => ({
    phase: p.phase,
    message: p.message,
    timestamp: p.timestamp,
    progress: p.progress,
    level: p.level,
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
  }));
  const opResult: OperationResult | null = rawResult ? {
    total_violations: rawResult.total_violations,
    fixable: rawResult.fixable,
    ai_candidate: rawResult.ai_candidate,
    ai_proposed: rawResult.ai_proposed ?? 0,
    ai_declined: rawResult.ai_declined ?? 0,
    ai_accepted: rawResult.ai_accepted ?? 0,
    manual_review: rawResult.manual_review,
    remediated_count: rawResult.remediated_count,
  } : null;

  const fetchData = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setScans([]);
    setViolations([]);
    setDependencies(null);
    try {
      const proj = await getProject(projectId);
      setProject(proj);
      const [scanResult, violResult, depsResult] = await Promise.allSettled([
        listProjectActivity(projectId, 20, 0),
        listProjectViolations(projectId, 100, 0),
        getProjectDependencies(projectId),
      ]);
      if (scanResult.status === 'fulfilled') setScans(scanResult.value.items);
      if (violResult.status === 'fulfilled') setViolations(violResult.value);
      if (depsResult.status === 'fulfilled') setDependencies(depsResult.value);
    } catch {
      setProject(null);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  useEffect(() => {
    if (opStatus === 'complete') fetchData();
  }, [opStatus, fetchData]);

  useEffect(() => {
    if (activeTab === 4 && projectId && !graphData && !graphLoading) {
      setGraphLoading(true);
      getProjectGraph(projectId).then(setGraphData).catch(() => setGraphData(null)).finally(() => setGraphLoading(false));
    }
  }, [activeTab, projectId, graphData, graphLoading]);

  const handleScan = useCallback((remediate: boolean) => {
    const colls = collections.split(',').map((c) => c.trim()).filter(Boolean);
    const opts: ProjectOperationOptions = {
      remediate,
      ansible_version: ansibleVersion || undefined,
      collection_specs: colls.length ? colls : undefined,
      enable_ai: enableAi,
      ai_model: enableAi ? (localStorage.getItem(AI_MODEL_STORAGE_KEY) ?? undefined) : undefined,
    };
    setActiveTab(0);
    startOperation(opts);
  }, [ansibleVersion, collections, enableAi, startOperation]);

  useEffect(() => {
    if ((searchParams.get('action') === 'check' || searchParams.get('action') === 'scan') && project && rawStatus === 'idle') {
      setSearchParams({}, { replace: true });
      handleScan(false);
    }
  }, [searchParams, project, rawStatus, setSearchParams, handleScan]);

  const handleDelete = useCallback(async () => {
    if (!projectId) return;
    if (!window.confirm('Delete this project and all its activity history?')) return;
    await deleteProject(projectId);
    navigate('/projects');
  }, [projectId, navigate]);

  const [editName, setEditName] = useState('');
  const [editUrl, setEditUrl] = useState('');
  const [editBranch, setEditBranch] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (project) {
      setEditName(project.name);
      setEditUrl(project.repo_url);
      setEditBranch(project.branch);
    }
  }, [project]);

  const handleSave = useCallback(async () => {
    if (!projectId || !project) return;
    setSaving(true);
    try {
      const updates: Record<string, string> = {};
      if (editName !== project.name) updates.name = editName;
      if (editUrl !== project.repo_url) updates.repo_url = editUrl;
      if (editBranch !== project.branch) updates.branch = editBranch;
      if (Object.keys(updates).length > 0) {
        await updateProject(projectId, updates);
        fetchData();
      }
    } finally {
      setSaving(false);
    }
  }, [projectId, project, editName, editUrl, editBranch, fetchData]);

  if (loading && !project) {
    return (
      <PageLayout>
        <PageHeader title="Project" />
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
      </PageLayout>
    );
  }

  if (!project) {
    return (
      <PageLayout>
        <PageHeader title="Project Not Found" />
        <div style={{ padding: 48, textAlign: 'center' }}>
          <p>This project does not exist.</p>
          <Button variant="primary" component={(props: object) => <Link {...props} to="/projects" />}>
            Back to Projects
          </Button>
        </div>
      </PageLayout>
    );
  }

  const isRunning = opStatus === 'connecting' || opStatus === 'preparing' || opStatus === 'checking' || opStatus === 'applying';
  const operationActive = isRunning || opStatus === 'awaiting_approval' || (opStatus === 'complete' && opResult != null) || opStatus === 'error';

  return (
    <PageLayout>
      <PageHeader
        title={project.name}
        description={`${project.repo_url} (${project.branch})`}
      />

      <div style={{ padding: '0 24px 24px' }}>
        <Tabs activeKey={activeTab} onSelect={(_e, k) => {
          setActiveTab(k as number);
          if (k === 1 || k === 2) fetchData();
          if (k === 4 && projectId && !graphData && !graphLoading) {
            setGraphLoading(true);
            getProjectGraph(projectId).then(setGraphData).catch(() => setGraphData(null)).finally(() => setGraphLoading(false));
          }
        }}>
          <Tab eventKey={0} title={<TabTitleText>Overview</TabTitleText>}>
            <div style={{ marginTop: 16 }}>
              {operationActive ? (
                <>
                  {isRunning && (
                    <OperationProgressPanel status={opStatus} progress={opProgress} onCancel={opCancel} />
                  )}

                  {opStatus === 'awaiting_approval' && opProposals.length > 0 && (
                    <ProposalReviewPanel proposals={opProposals} onApprove={opApprove} feedbackEnabled={feedbackEnabled} scanId={opScanId ?? undefined} />
                  )}

                  {opStatus === 'complete' && opResult && (
                    <OperationResultCard result={opResult} onDismiss={opReset} />
                  )}

                  {opStatus === 'error' && (
                    <Card style={{ marginBottom: 16, borderLeft: '4px solid var(--pf-t--global--color--status--danger--default)' }}>
                      <CardBody>
                        <h3 style={{ color: 'var(--pf-t--global--color--status--danger--default)' }}>Error</h3>
                        <p>{opError}</p>
                        <Button variant="link" onClick={opReset}>Dismiss</Button>
                      </CardBody>
                    </Card>
                  )}
                </>
              ) : project.scan_count === 0 ? (
                <Card style={{ marginBottom: 16 }}>
                  <CardBody style={{ textAlign: 'center', padding: '48px 24px' }}>
                    <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>No checks yet</div>
                    <div style={{ opacity: 0.7 }}>
                      Go to the <Button variant="link" isInline onClick={() => setActiveTab(1)}>Activity</Button> tab to run the first check.
                    </div>
                  </CardBody>
                </Card>
              ) : (
                <>
                  <Split hasGutter style={{ marginBottom: 16 }}>
                    <SplitItem>
                      <Card>
                        <CardBody>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: 36, fontWeight: 700, color: healthColor(project.health_score) }}>{project.health_score}</div>
                            <div style={{ opacity: 0.7 }}>Health Score</div>
                          </div>
                        </CardBody>
                      </Card>
                    </SplitItem>
                    <SplitItem>
                      <Card>
                        <CardBody>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: 36, fontWeight: 700 }}>{project.total_violations}</div>
                            <div style={{ opacity: 0.7 }}>Violations</div>
                          </div>
                        </CardBody>
                      </Card>
                    </SplitItem>
                    <SplitItem>
                      <Card>
                        <CardBody>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: 36, fontWeight: 700 }}>{project.scan_count}</div>
                            <div style={{ opacity: 0.7 }}>Activity</div>
                          </div>
                        </CardBody>
                      </Card>
                    </SplitItem>
                    <SplitItem>
                      <Card>
                        <CardBody>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: 36, fontWeight: 700 }}>
                              {project.last_scanned_at ? timeAgo(project.last_scanned_at) : 'Never'}
                            </div>
                            <div style={{ opacity: 0.7 }}>Last Checked</div>
                          </div>
                        </CardBody>
                      </Card>
                    </SplitItem>
                  </Split>

                  {Object.keys(project.severity_breakdown).length > 0 && (
                    <Card style={{ marginBottom: 16 }}>
                      <CardBody>
                        <h3>Severity Breakdown</h3>
                        <Flex gap={{ default: 'gapLg' }} style={{ marginTop: 8 }}>
                          {(() => {
                            const merged = new Map<string, number>();
                            for (const [level, count] of Object.entries(project.severity_breakdown)) {
                              const cls = severityClass(level);
                              merged.set(cls, (merged.get(cls) ?? 0) + count);
                            }
                            return Array.from(merged.entries())
                              .sort((a, b) => severityOrder(a[0]) - severityOrder(b[0]))
                              .map(([cls, count]) => (
                                <FlexItem key={cls}>
                                  <span className={`apme-severity ${cls}`}>
                                    {SEVERITY_LABELS[cls] ?? cls}: {count}
                                  </span>
                                </FlexItem>
                              ));
                          })()}
                        </Flex>
                      </CardBody>
                    </Card>
                  )}
                </>
              )}
            </div>
          </Tab>

          <Tab eventKey={1} title={<TabTitleText>Activity</TabTitleText>}>
            <div style={{ marginTop: 16 }}>
              <Card style={{ marginBottom: 16 }}>
                <CardBody>
                  <h3>Options</h3>
                  <CheckOptionsForm
                    ansibleVersion={ansibleVersion}
                    onAnsibleVersionChange={setAnsibleVersion}
                    collections={collections}
                    onCollectionsChange={setCollections}
                    enableAi={enableAi}
                    onEnableAiChange={setEnableAi}
                    idPrefix="proj"
                  />
                  <Flex gap={{ default: 'gapSm' }} style={{ marginTop: 12 }}>
                    <Button variant="primary" isDisabled={isRunning} onClick={() => handleScan(false)}>Check</Button>
                    <Button variant="secondary" isDisabled={isRunning} onClick={() => handleScan(true)}>Remediate</Button>
                    {isRunning && <Button variant="link" onClick={opCancel}>Cancel</Button>}
                  </Flex>
                </CardBody>
              </Card>

              <h3 style={{ marginBottom: 8 }}>History</h3>
              {scans.length === 0 ? (
                <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>No activity recorded yet.</div>
              ) : (
                <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid">
                  <thead>
                    <tr role="row">
                      <th role="columnheader">Type</th>
                      <th role="columnheader">Status</th>
                      <th role="columnheader">Violations</th>
                      <th role="columnheader">Fixable</th>
                      <th role="columnheader">Remediated</th>
                      <th role="columnheader">AI Proposed</th>
                      <th role="columnheader">AI Declined</th>
                      <th role="columnheader">AI Accepted</th>
                      <th role="columnheader">Manual</th>
                      <th role="columnheader">Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scans.map((scan) => {
                      const isFix = scan.scan_type === 'fix' || scan.scan_type === 'remediate';
                      return (
                      <tr
                        key={scan.scan_id}
                        role="row"
                        tabIndex={0}
                        onClick={() => navigate(`/activity/${scan.scan_id}`)}
                        onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/activity/${scan.scan_id}`); }}
                        style={{ cursor: 'pointer' }}
                      >
                        <td role="cell">
                          <span className={`apme-badge ${isFix ? 'passed' : 'running'}`}>
                            {scan.scan_type === 'scan' ? 'check' : scan.scan_type === 'fix' ? 'remediate' : scan.scan_type}
                          </span>
                        </td>
                        <td role="cell"><StatusBadge violations={scan.total_violations} scanType={scan.scan_type} /></td>
                        <td role="cell">{scan.total_violations}</td>
                        <td role="cell">
                          {isFix
                            ? <span>{0}</span>
                            : <span className="apme-count-success">{scan.fixable}</span>
                          }
                        </td>
                        <td role="cell"><span className="apme-count-success">{scan.remediated_count}</span></td>
                        <td role="cell">{scan.ai_proposed ?? 0}</td>
                        <td role="cell">{scan.ai_declined ?? 0}</td>
                        <td role="cell"><span className="apme-count-success">{scan.ai_accepted ?? 0}</span></td>
                        <td role="cell"><span className="apme-count-warning">{scan.manual_review}</span></td>
                        <td role="cell" style={{ opacity: 0.7 }}>{timeAgo(scan.created_at)}</td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </Tab>

          <Tab eventKey={2} title={<TabTitleText>Violations</TabTitleText>}>
            <ViolationsTab violations={violations} />
          </Tab>

          <Tab eventKey={3} title={<TabTitleText>Dependencies</TabTitleText>}>
            <DependenciesTab dependencies={dependencies} loading={loading} />
          </Tab>

          <Tab eventKey={4} title={<TabTitleText>Visualize</TabTitleText>}>
            <div style={{ marginTop: 16 }}>
              {graphLoading ? (
                <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading graph...</div>
              ) : graphData ? (
                <GraphVisualization data={graphData} />
              ) : (
                <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>
                  No graph data available. Run a check to generate the content graph.
                </div>
              )}
            </div>
          </Tab>

          <Tab eventKey={5} title={<TabTitleText>Settings</TabTitleText>}>
            <div style={{ marginTop: 16, maxWidth: 600 }}>
              <Card>
                <CardBody>
                  <Flex direction={{ default: 'column' }} gap={{ default: 'gapMd' }}>
                    <FlexItem>
                      <label htmlFor="edit-name" style={{ display: 'block', fontWeight: 600, marginBottom: 4 }}>Name</label>
                      <TextInput id="edit-name" value={editName} onChange={(_e, v) => setEditName(v)} />
                    </FlexItem>
                    <FlexItem>
                      <label htmlFor="edit-url" style={{ display: 'block', fontWeight: 600, marginBottom: 4 }}>Repository URL</label>
                      <TextInput id="edit-url" value={editUrl} onChange={(_e, v) => setEditUrl(v)} />
                    </FlexItem>
                    <FlexItem>
                      <label htmlFor="edit-branch" style={{ display: 'block', fontWeight: 600, marginBottom: 4 }}>Branch</label>
                      <TextInput id="edit-branch" value={editBranch} onChange={(_e, v) => setEditBranch(v)} />
                    </FlexItem>
                    <FlexItem>
                      <Flex gap={{ default: 'gapSm' }}>
                        <Button variant="primary" onClick={handleSave} isDisabled={saving}>
                          {saving ? 'Saving...' : 'Save'}
                        </Button>
                        <Button variant="danger" onClick={handleDelete}>Delete Project</Button>
                      </Flex>
                    </FlexItem>
                  </Flex>
                </CardBody>
              </Card>
            </div>
          </Tab>
        </Tabs>
      </div>
    </PageLayout>
  );
}

function ViolationsTab({ violations }: { violations: ViolationDetail[] }) {
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());

  const sorted = useMemo(
    () =>
      [...violations].sort(
        (a, b) =>
          severityOrder(severityClass(a.level, a.rule_id)) -
          severityOrder(severityClass(b.level, b.rule_id)),
      ),
    [violations],
  );

  const toggleRow = (id: number) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div style={{ marginTop: 16 }}>
      {sorted.length === 0 ? (
        <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>
          No violations in the latest check.
        </div>
      ) : (
        <table
          className="pf-v6-c-table pf-m-compact pf-m-grid-md apme-violations-table"
          role="grid"
        >
          <thead>
            <tr role="row">
              <th role="columnheader" className="apme-vt-col-rule">Rule</th>
              <th role="columnheader" className="apme-vt-col-severity">Severity</th>
              <th role="columnheader" className="apme-vt-col-file">File</th>
              <th role="columnheader" className="apme-vt-col-line">Line</th>
              <th role="columnheader" className="apme-vt-col-message">Message</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((v) => {
              const isExpanded = expandedRows.has(v.id);
              return (
                <tr key={v.id} role="row">
                  <td role="cell">
                    <span className="apme-rule-id">{bareRuleId(v.rule_id)}</span>
                  </td>
                  <td role="cell">
                    <span
                      className={`apme-severity ${severityClass(v.level, v.rule_id)}`}
                      style={{ whiteSpace: 'nowrap' }}
                    >
                      {severityLabel(v.level, v.rule_id)}
                    </span>
                  </td>
                  <td role="cell" style={{ fontFamily: 'var(--pf-t--global--font--family--mono)' }}>
                    {v.file}
                  </td>
                  <td role="cell">{v.line ?? ''}</td>
                  <td
                    role="cell"
                    className={`apme-vt-message${isExpanded ? ' expanded' : ''}`}
                    onClick={() => toggleRow(v.id)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') toggleRow(v.id); }}
                    tabIndex={0}
                  >
                    {v.message}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function DependenciesTab({ dependencies, loading }: { dependencies: ProjectDependencies | null; loading: boolean }) {
  const navigate = useNavigate();

  if (loading && !dependencies) {
    return (
      <div style={{ marginTop: 16, padding: 24, textAlign: 'center', opacity: 0.6 }}>
        Loading dependencies...
      </div>
    );
  }

  if (!dependencies) {
    return (
      <div style={{ marginTop: 16, padding: 24, textAlign: 'center', opacity: 0.6 }}>
        No dependency information available. Run a check to collect dependencies.
      </div>
    );
  }

  const hasCollections = dependencies.collections.length > 0;
  const hasPackages = dependencies.python_packages.length > 0;
  const hasAnsibleCore = !!dependencies.ansible_core_version;

  if (!hasCollections && !hasPackages && !hasAnsibleCore) {
    return (
      <div style={{ marginTop: 16, padding: 24, textAlign: 'center', opacity: 0.6 }}>
        No dependency information available. Run a check to collect dependencies.
      </div>
    );
  }

  return (
    <div style={{ marginTop: 16 }}>
      {hasAnsibleCore && (
        <Card style={{ marginBottom: 16 }}>
          <CardBody>
            <h3 style={{ marginBottom: 8 }}>Ansible Core</h3>
            <div style={{ fontFamily: 'var(--pf-t--global--font--family--mono)' }}>
              ansible-core=={dependencies.ansible_core_version}
            </div>
          </CardBody>
        </Card>
      )}

      {hasCollections && (
        <Card style={{ marginBottom: 16 }}>
          <CardBody>
            <h3 style={{ marginBottom: 8 }}>Collections ({dependencies.collections.length})</h3>
            <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid">
              <thead>
                <tr role="row">
                  <th role="columnheader">FQCN</th>
                  <th role="columnheader">Version</th>
                  <th role="columnheader">Source</th>
                </tr>
              </thead>
              <tbody>
                {dependencies.collections.map((c) => (
                  <tr
                    key={`${c.fqcn}-${c.version}`}
                    role="row"
                    tabIndex={0}
                    style={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/collections/${encodeURIComponent(c.fqcn)}`)}
                    onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/collections/${encodeURIComponent(c.fqcn)}`); }}
                  >
                    <td role="cell" style={{ fontFamily: 'var(--pf-t--global--font--family--mono)', fontWeight: 600 }}>
                      {c.fqcn}
                    </td>
                    <td role="cell">{c.version}</td>
                    <td role="cell" style={{ opacity: 0.7 }}>{c.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}

      {hasPackages && (
        <Card style={{ marginBottom: 16 }}>
          <CardBody>
            <h3 style={{ marginBottom: 8 }}>Python Packages ({dependencies.python_packages.length})</h3>
            <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid">
              <thead>
                <tr role="row">
                  <th role="columnheader">Package</th>
                  <th role="columnheader">Version</th>
                </tr>
              </thead>
              <tbody>
                {dependencies.python_packages.map((p) => (
                  <tr
                    key={`${p.name}-${p.version}`}
                    role="row"
                    tabIndex={0}
                    style={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/python-packages/${encodeURIComponent(p.name)}`)}
                    onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/python-packages/${encodeURIComponent(p.name)}`); }}
                  >
                    <td role="cell" style={{ fontFamily: 'var(--pf-t--global--font--family--mono)', fontWeight: 600 }}>
                      {p.name}
                    </td>
                    <td role="cell">{p.version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}

      {dependencies.requirements_files.length > 0 && (
        <Card>
          <CardBody>
            <h3 style={{ marginBottom: 8 }}>Requirements Files</h3>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {dependencies.requirements_files.map((f) => (
                <li key={f} style={{ fontFamily: 'var(--pf-t--global--font--family--mono)' }}>{f}</li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
