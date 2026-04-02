import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import { Button, Pagination } from '@patternfly/react-core';
import { listSessions } from '../services/api';
import type { SessionSummary } from '../types/api';
import { timeAgo } from '../services/format';

const PAGE_SIZE = 20;

export function SessionsPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<SessionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchSessions = useCallback(() => {
    setLoading(true);
    setError(false);
    const offset = (page - 1) * PAGE_SIZE;
    listSessions(PAGE_SIZE, offset)
      .then((data) => {
        setItems(data.items);
        setTotal(data.total);
      })
      .catch(() => {
        setItems([]);
        setTotal(0);
        setError(true);
      })
      .finally(() => setLoading(false));
  }, [page]);

  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  return (
    <PageLayout>
      <PageHeader
        title="Sessions"
        description="CLI sessions grouped by project path"
      />

      {loading ? (
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
      ) : error ? (
        <div style={{ padding: 48, textAlign: 'center' }}>
          <p style={{ opacity: 0.6, marginBottom: 12 }}>Failed to load sessions.</p>
          <Button variant="link" onClick={fetchSessions}>Retry</Button>
        </div>
      ) : items.length === 0 ? (
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>
          No sessions recorded yet. Sessions are created when the CLI scans a project.
        </div>
      ) : (
        <div style={{ padding: '0 24px 24px' }}>
          <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid">
            <thead>
              <tr role="row">
                <th role="columnheader">Session ID</th>
                <th role="columnheader">Project Path</th>
                <th role="columnheader">First Seen</th>
                <th role="columnheader">Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr
                  key={s.session_id}
                  role="row"
                  tabIndex={0}
                  onClick={() => navigate(`/sessions/${s.session_id}`)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/sessions/${s.session_id}`); } }}
                  style={{ cursor: 'pointer' }}
                >
                  <td role="cell" style={{ fontFamily: 'var(--pf-t--global--font--family--mono)', fontSize: 13 }}>
                    {s.session_id.slice(0, 12)}...
                  </td>
                  <td role="cell" style={{ fontFamily: 'var(--pf-t--global--font--family--mono)' }}>
                    {s.project_path}
                  </td>
                  <td role="cell" style={{ opacity: 0.7 }}>{timeAgo(s.first_seen)}</td>
                  <td role="cell" style={{ opacity: 0.7 }}>{timeAgo(s.last_seen)}</td>
                </tr>
              ))}
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
