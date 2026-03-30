import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import {
  Button,
  Card,
  CardBody,
  Label,
  Split,
  SplitItem,
} from '@patternfly/react-core';
import { getPythonPackageDetail } from '../services/api';
import type { PythonPackageDetail } from '../types/api';
import { healthLabelColor } from '../components/severity';

function HealthBadge({ score }: { score: number }) {
  return <Label color={healthLabelColor(score)} isCompact>{score}</Label>;
}

export function PythonPackageDetailPage() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const [pkg, setPkg] = useState<PythonPackageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchData = useCallback(async () => {
    if (!name) return;
    setLoading(true);
    setError(false);
    try {
      const data = await getPythonPackageDetail(name);
      setPkg(data);
    } catch {
      setError(true);
      setPkg(null);
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) {
    return (
      <PageLayout>
        <PageHeader title="Python Package" />
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
      </PageLayout>
    );
  }

  if (error || !pkg) {
    return (
      <PageLayout>
        <PageHeader title="Package Not Found" />
        <div style={{ padding: 48, textAlign: 'center' }}>
          <p>This package does not exist or has not been scanned yet.</p>
          <Button variant="primary" component={(props: object) => <Link {...props} to="/python-packages" />}>
            Back to Python Packages
          </Button>
        </div>
      </PageLayout>
    );
  }

  return (
    <PageLayout>
      <PageHeader
        title={pkg.name}
        description="Python package"
      />

      <div style={{ padding: '0 24px 24px' }}>
        <Split hasGutter style={{ marginBottom: 16 }}>
          <SplitItem>
            <Card>
              <CardBody>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 36, fontWeight: 700 }}>{pkg.project_count}</div>
                  <div style={{ opacity: 0.7 }}>Projects Using</div>
                </div>
              </CardBody>
            </Card>
          </SplitItem>
          <SplitItem>
            <Card>
              <CardBody>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 36, fontWeight: 700 }}>{pkg.versions.length}</div>
                  <div style={{ opacity: 0.7 }}>Versions</div>
                </div>
              </CardBody>
            </Card>
          </SplitItem>
        </Split>

        {pkg.versions.length > 0 && (
          <Card style={{ marginBottom: 16 }}>
            <CardBody>
              <h3 style={{ marginBottom: 8 }}>Versions in Use</h3>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {pkg.versions.map((v) => (
                  <Label key={v} isCompact>{v}</Label>
                ))}
              </div>
            </CardBody>
          </Card>
        )}

        <Card>
          <CardBody>
            <h3 style={{ marginBottom: 8 }}>Projects ({pkg.projects.length})</h3>
            {pkg.projects.length === 0 ? (
              <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>
                No projects are using this package.
              </div>
            ) : (
              <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid">
                <thead>
                  <tr role="row">
                    <th role="columnheader">Project</th>
                    <th role="columnheader">Health</th>
                    <th role="columnheader">Package Version</th>
                  </tr>
                </thead>
                <tbody>
                  {pkg.projects.map((proj) => (
                    <tr
                      key={proj.id}
                      role="row"
                      tabIndex={0}
                      style={{ cursor: 'pointer' }}
                      onClick={() => navigate(`/projects/${proj.id}`)}
                      onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/projects/${proj.id}`); }}
                    >
                      <td role="cell" style={{ fontWeight: 600 }}>{proj.name}</td>
                      <td role="cell"><HealthBadge score={proj.health_score} /></td>
                      <td role="cell">
                        <Label isCompact>{proj.package_version}</Label>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardBody>
        </Card>
      </div>
    </PageLayout>
  );
}
