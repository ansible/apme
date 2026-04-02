import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import {
  Button,
  Card,
  CardBody,
  Split,
  SplitItem,
} from '@patternfly/react-core';
import { getSession, getSessionTrend } from '../services/api';
import type { SessionDetail, TrendPoint } from '../types/api';
import { StatusBadge } from '../components/StatusBadge';
import { timeAgo } from '../services/format';
import { TrendChart } from '../components/TrendChart';

export function SessionDetailPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchData = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(false);
    try {
      const [sess, trendData] = await Promise.all([
        getSession(sessionId),
        getSessionTrend(sessionId).catch(() => [] as TrendPoint[]),
      ]);
      setSession(sess);
      setTrend(trendData);
    } catch {
      setError(true);
      setSession(null);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) {
    return (
      <PageLayout>
        <PageHeader title="Session" />
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
      </PageLayout>
    );
  }

  if (error || !session) {
    return (
      <PageLayout>
        <PageHeader title="Session Not Found" />
        <div style={{ padding: 48, textAlign: 'center' }}>
          <p>This session does not exist.</p>
          <Button variant="primary" component={(props: object) => <Link {...props} to="/sessions" />}>
            Back to Sessions
          </Button>
        </div>
      </PageLayout>
    );
  }

  return (
    <PageLayout>
      <PageHeader
        title={session.project_path}
        description={`Session ${session.session_id.slice(0, 12)}...`}
        breadcrumbs={[
          { label: 'Sessions', to: '/sessions' },
          { label: session.project_path },
        ]}
      />

      <div style={{ padding: '0 24px 24px' }}>
        <Split hasGutter style={{ marginBottom: 16 }}>
          <SplitItem>
            <Card>
              <CardBody>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 36, fontWeight: 700 }}>{session.scans.length}</div>
                  <div style={{ opacity: 0.7 }}>Scans</div>
                </div>
              </CardBody>
            </Card>
          </SplitItem>
          <SplitItem>
            <Card>
              <CardBody>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 18, fontWeight: 600 }}>{timeAgo(session.first_seen)}</div>
                  <div style={{ opacity: 0.7 }}>First Seen</div>
                </div>
              </CardBody>
            </Card>
          </SplitItem>
          <SplitItem>
            <Card>
              <CardBody>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 18, fontWeight: 600 }}>{timeAgo(session.last_seen)}</div>
                  <div style={{ opacity: 0.7 }}>Last Seen</div>
                </div>
              </CardBody>
            </Card>
          </SplitItem>
        </Split>

        {trend.length > 1 && <TrendChart data={trend} style={{ marginBottom: 16 }} />}

        <h3 style={{ marginBottom: 8 }}>Activity ({session.scans.length})</h3>
        {session.scans.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>No scans in this session yet.</div>
        ) : (
          <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid">
            <thead>
              <tr role="row">
                <th role="columnheader">Type</th>
                <th role="columnheader">Status</th>
                <th role="columnheader">Violations</th>
                <th role="columnheader">Fixable</th>
                <th role="columnheader">Remediated</th>
                <th role="columnheader">Time</th>
              </tr>
            </thead>
            <tbody>
              {session.scans.map((scan) => {
                const isFix = scan.scan_type === 'fix' || scan.scan_type === 'remediate';
                return (
                  <tr
                    key={scan.scan_id}
                    role="row"
                    tabIndex={0}
                    onClick={() => navigate(`/activity/${scan.scan_id}`)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/activity/${scan.scan_id}`); } }}
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
                        ? <span style={{ opacity: 0.3 }}>&mdash;</span>
                        : <span className="apme-count-success">{scan.fixable}</span>
                      }
                    </td>
                    <td role="cell">
                      {isFix
                        ? <span className="apme-count-success">{scan.remediated_count}</span>
                        : <span style={{ opacity: 0.3 }}>&mdash;</span>
                      }
                    </td>
                    <td role="cell" style={{ opacity: 0.7 }}>{timeAgo(scan.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </PageLayout>
  );
}
