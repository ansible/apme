import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import { Button, Pagination } from '@patternfly/react-core';
import { listActivity } from '../services/api';
import type { ActivitySummary } from '../types/api';
import { timeAgo } from '../services/format';
import {
  fetchProjectOperationState,
  LIVE_OPERATION_STATUSES,
} from '../hooks/useProjectOperationState';
import { AI_MODEL_STORAGE_KEY } from './SettingsPage';

const PAGE_SIZE = 20;

export function ActivityPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const sessionFilter = searchParams.get('session_id') ?? undefined;
  const [items, setItems] = useState<ActivitySummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  /** scan_id of the latest row when a matching live op exists. */
  const [resumableScanId, setResumableScanId] = useState<string | null>(null);
  const [startOverBusy, setStartOverBusy] = useState(false);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        setRefreshKey((k) => k + 1);
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, []);

  useEffect(() => {
    setPage(1);
  }, [sessionFilter]);

  const fetchActivity = useCallback(() => {
    setLoading(true);
    const offset = (page - 1) * PAGE_SIZE;
    listActivity(PAGE_SIZE, offset, sessionFilter)
      .then((data) => {
        setItems(data.items);
        setTotal(data.total);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [page, sessionFilter]);

  useEffect(() => { fetchActivity(); }, [fetchActivity, refreshKey]);

  useEffect(() => {
    let cancelled = false;
    setResumableScanId(null);
    const latest = items[0];
    if (page !== 1 || !latest?.project_id) return;

    const projectId = latest.project_id;
    const scanId = latest.scan_id;
    fetchProjectOperationState(projectId).then((op) => {
      if (cancelled || !op) return;
      if (op.scan_id === scanId && LIVE_OPERATION_STATUSES.has(op.status)) {
        setResumableScanId(scanId);
      }
    });
    return () => { cancelled = true; };
  }, [items, page, refreshKey]);

  const handleResume = useCallback(
    (e: React.MouseEvent, projectId: string) => {
      e.stopPropagation();
      navigate(`/projects/${projectId}?resume=1`);
    },
    [navigate],
  );

  const handleStartOver = useCallback(
    async (e: React.MouseEvent, item: ActivitySummary) => {
      e.stopPropagation();
      if (!item.project_id || startOverBusy) return;
      const ok = window.confirm(
        'Discard the current interactive session and start a new scan?',
      );
      if (!ok) return;

      setStartOverBusy(true);
      try {
        // Always Scan (check + assess_pause); do not inherit remediate from history.
        // Match Project Scan defaults: enable_ai on, optional stored model.
        const aiModel = localStorage.getItem(AI_MODEL_STORAGE_KEY) ?? undefined;
        const res = await fetch(`/api/v1/projects/${item.project_id}/operation`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
          },
          body: JSON.stringify({
            action: 'check',
            abandon_working_set: true,
            options: {
              assess_pause: true,
              interactive: true,
              enable_ai: true,
              ...(aiModel ? { ai_model: aiModel } : {}),
            },
          }),
        });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`${res.status}: ${text}`);
        }
        navigate(`/projects/${item.project_id}?resume=1`);
      } catch (err) {
        console.error('Failed to start over:', err);
        window.alert('Could not start over. Try again from the project page.');
      } finally {
        setStartOverBusy(false);
      }
    },
    [navigate, startOverBusy],
  );

  return (
    <PageLayout>
      <PageHeader title="Activity" />

      {loading ? (
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
      ) : items.length === 0 ? (
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>No activity recorded.</div>
      ) : (
        <div style={{ padding: '0 24px 24px' }}>
          <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid">
            <thead>
              <tr role="row">
                <th role="columnheader">Project</th>
                <th role="columnheader">Source</th>
                <th role="columnheader">Type</th>
                <th role="columnheader">Status</th>
                <th role="columnheader">Violations</th>
                <th role="columnheader">Fixable</th>
                <th role="columnheader">Remediated</th>
                <th role="columnheader" title="AI proposals offered">AI Proposed</th>
                <th role="columnheader" title="AI could not fix">AI Declined</th>
                <th role="columnheader" title="AI proposals accepted">AI Accepted</th>
                <th role="columnheader">Manual</th>
                <th role="columnheader">Time</th>
                <th role="columnheader">
                  <span className="pf-v6-screen-reader">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const isRemediate = item.scan_type === 'fix' || item.scan_type === 'remediate';
                const isResumable = item.scan_id === resumableScanId && Boolean(item.project_id);
                return (
                <tr
                  key={item.scan_id}
                  role="row"
                  tabIndex={0}
                  onClick={() => navigate(`/activity/${item.scan_id}`)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/activity/${item.scan_id}`); } }}
                  style={{ cursor: 'pointer' }}
                >
                  <td role="cell" style={{ fontFamily: 'var(--pf-t--global--font--family--mono)' }}>
                    {item.project_path}
                  </td>
                  <td role="cell">
                    <span className="apme-badge running">{item.source}</span>
                  </td>
                  <td role="cell">
                    <span className={`apme-badge ${isRemediate ? 'passed' : 'running'}`}>
                      {item.scan_type === 'scan' ? 'check' : item.scan_type === 'fix' ? 'remediate' : item.scan_type}
                    </span>
                  </td>
                  <td role="cell">
                    {isResumable ? (
                      <span className="apme-badge passed">Available</span>
                    ) : (
                      <span className="apme-badge" style={{ opacity: 0.75 }}>Read-only</span>
                    )}
                  </td>
                  <td role="cell">{item.total_violations}</td>
                  <td role="cell">
                    {isRemediate
                      ? <span style={{ opacity: 0.3 }}>&mdash;</span>
                      : <span className="apme-count-success">{item.fixable ?? ''}</span>
                    }
                  </td>
                  <td role="cell">
                    {isRemediate
                      ? <span className="apme-count-success">{item.remediated_count ?? 0}</span>
                      : <span style={{ opacity: 0.3 }}>&mdash;</span>
                    }
                  </td>
                  <td role="cell">{item.ai_proposed ?? 0}</td>
                  <td role="cell">{item.ai_declined ?? 0}</td>
                  <td role="cell"><span className="apme-count-success">{item.ai_accepted ?? 0}</span></td>
                  <td role="cell"><span className="apme-count-warning">{item.manual_review ?? ''}</span></td>
                  <td role="cell" style={{ opacity: 0.7 }}>{timeAgo(item.created_at)}</td>
                  <td role="cell" onClick={(e) => e.stopPropagation()}>
                    {isResumable && item.project_id ? (
                      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={(e) => handleResume(e, item.project_id!)}
                        >
                          Resume
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          isDisabled={startOverBusy}
                          onClick={(e) => handleStartOver(e, item)}
                        >
                          Start over
                        </Button>
                      </div>
                    ) : null}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
          {total > PAGE_SIZE && (
            <Pagination
              itemCount={total}
              perPage={PAGE_SIZE}
              page={page}
              onSetPage={(_e, p) => setPage(p)}
              style={{ marginTop: 16 }}
            />
          )}
        </div>
      )}
    </PageLayout>
  );
}
