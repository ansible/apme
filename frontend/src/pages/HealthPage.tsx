import { useEffect, useState } from 'react';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import { Button, Label } from '@patternfly/react-core';
import { SyncAltIcon } from '@patternfly/react-icons';
import { getHealth } from '../services/api';
import type { HealthStatus } from '../types/api';

export function HealthPage() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const isOk = (status: string) => status === 'ok';

  return (
    <PageLayout>
      <PageHeader
        title="System Health"
        headerActions={
          <Button variant="secondary" icon={<SyncAltIcon />} onClick={load}>
            Refresh
          </Button>
        }
      />

      {loading ? (
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Checking health...</div>
      ) : !health ? (
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Unable to reach gateway.</div>
      ) : (
        <div style={{ padding: '0 24px 24px' }}>
          <table className="pf-v6-c-table pf-m-compact" role="grid">
            <thead>
              <tr role="row">
                <th role="columnheader">Component</th>
                <th role="columnheader">Address</th>
                <th role="columnheader">Status</th>
              </tr>
            </thead>
            <tbody>
              <tr role="row">
                <td role="cell">Gateway</td>
                <td role="cell" style={{ opacity: 0.7 }}>this service</td>
                <td role="cell">
                  <Label color={isOk(health.status) ? 'green' : 'red'} isCompact>
                    {health.status}
                  </Label>
                </td>
              </tr>
              <tr role="row">
                <td role="cell">Database</td>
                <td role="cell" style={{ opacity: 0.7 }}>{health.database_type || 'unknown'}</td>
                <td role="cell">
                  <Label color={isOk(health.database) ? 'green' : 'red'} isCompact>
                    {health.database}
                  </Label>
                </td>
              </tr>
              {health.components.map((c) => (
                <tr key={c.name} role="row">
                  <td role="cell">{c.name}</td>
                  <td role="cell" style={{ fontFamily: 'var(--pf-t--global--font--family--mono)', opacity: 0.7 }}>{c.address}</td>
                  <td role="cell">
                    <Label color={isOk(c.status) ? 'green' : 'red'} isCompact>
                      {c.status}
                    </Label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </PageLayout>
  );
}
