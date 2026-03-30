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
import { getCollectionDetail } from '../services/api';
import type { CollectionDetail } from '../types/api';
import { healthLabelColor } from '../components/severity';

function HealthBadge({ score }: { score: number }) {
  return <Label color={healthLabelColor(score)} isCompact>{score}</Label>;
}

export function CollectionDetailPage() {
  const { fqcn } = useParams<{ fqcn: string }>();
  const navigate = useNavigate();
  const [collection, setCollection] = useState<CollectionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchData = useCallback(async () => {
    if (!fqcn) return;
    setLoading(true);
    setError(false);
    try {
      const data = await getCollectionDetail(fqcn);
      setCollection(data);
    } catch {
      setError(true);
      setCollection(null);
    } finally {
      setLoading(false);
    }
  }, [fqcn]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) {
    return (
      <PageLayout>
        <PageHeader title="Collection" />
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
      </PageLayout>
    );
  }

  if (error || !collection) {
    return (
      <PageLayout>
        <PageHeader title="Collection Not Found" />
        <div style={{ padding: 48, textAlign: 'center' }}>
          <p>This collection does not exist or has not been scanned yet.</p>
          <Button variant="primary" component={(props: object) => <Link {...props} to="/collections" />}>
            Back to Collections
          </Button>
        </div>
      </PageLayout>
    );
  }

  return (
    <PageLayout>
      <PageHeader
        title={collection.fqcn}
        description={`Source: ${collection.source}`}
      />

      <div style={{ padding: '0 24px 24px' }}>
        <Split hasGutter style={{ marginBottom: 16 }}>
          <SplitItem>
            <Card>
              <CardBody>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 36, fontWeight: 700 }}>{collection.project_count}</div>
                  <div style={{ opacity: 0.7 }}>Projects Using</div>
                </div>
              </CardBody>
            </Card>
          </SplitItem>
          <SplitItem>
            <Card>
              <CardBody>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 36, fontWeight: 700 }}>{collection.versions.length}</div>
                  <div style={{ opacity: 0.7 }}>Versions</div>
                </div>
              </CardBody>
            </Card>
          </SplitItem>
        </Split>

        {collection.versions.length > 0 && (
          <Card style={{ marginBottom: 16 }}>
            <CardBody>
              <h3 style={{ marginBottom: 8 }}>Versions in Use</h3>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {collection.versions.map((v) => (
                  <Label key={v} isCompact>{v}</Label>
                ))}
              </div>
            </CardBody>
          </Card>
        )}

        <Card>
          <CardBody>
            <h3 style={{ marginBottom: 8 }}>Projects ({collection.projects.length})</h3>
            {collection.projects.length === 0 ? (
              <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>
                No projects are using this collection.
              </div>
            ) : (
              <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid">
                <thead>
                  <tr role="row">
                    <th role="columnheader">Project</th>
                    <th role="columnheader">Health</th>
                    <th role="columnheader">Collection Version</th>
                  </tr>
                </thead>
                <tbody>
                  {collection.projects.map((proj) => (
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
                        <Label isCompact>{proj.collection_version}</Label>
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
