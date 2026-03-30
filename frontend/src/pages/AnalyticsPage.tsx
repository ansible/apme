import { useEffect, useState } from 'react';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import {
  Card,
  CardBody,
  CardHeader,
  Label,
  Title,
} from '@patternfly/react-core';
import { getTopViolations, getRemediationRates, getAiAcceptance } from '../services/api';
import type { TopViolation, RemediationRateEntry, AiAcceptanceEntry } from '../types/api';

export function AnalyticsPage() {
  const [topViolations, setTopViolations] = useState<TopViolation[]>([]);
  const [remediationRates, setRemediationRates] = useState<RemediationRateEntry[]>([]);
  const [aiAcceptance, setAiAcceptance] = useState<AiAcceptanceEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      getTopViolations(20),
      getRemediationRates(20),
      getAiAcceptance(),
    ])
      .then(([violations, rates, acceptance]) => {
        setTopViolations(violations);
        setRemediationRates(rates);
        setAiAcceptance(acceptance);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <PageLayout>
        <PageHeader title="Analytics" />
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
      </PageLayout>
    );
  }

  return (
    <PageLayout>
      <PageHeader
        title="Analytics"
        description="Violation statistics, remediation rates, and AI performance"
      />

      <div className="apme-analytics-grid">
        <Card>
          <CardHeader>
            <Title headingLevel="h3" size="lg">Top Violations</Title>
          </CardHeader>
          <CardBody style={{ paddingInline: 0 }}>
            {topViolations.length === 0 ? (
              <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>
                No violation data yet. Run checks to collect statistics.
              </div>
            ) : (
              <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid" style={{ tableLayout: 'auto' }}>
                <thead>
                  <tr role="row">
                    <th role="columnheader" style={{ paddingLeft: 24 }}>Rule ID</th>
                    <th role="columnheader" style={{ paddingRight: 24, textAlign: 'right' }}>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {topViolations.map((v, i) => (
                    <tr key={v.rule_id} role="row">
                      <td role="cell" style={{ paddingLeft: 24 }}>
                        <span style={{ fontFamily: 'var(--pf-t--global--font--family--mono)' }}>
                          {v.rule_id}
                        </span>
                      </td>
                      <td role="cell" style={{ paddingRight: 24, textAlign: 'right' }}>
                        <Label
                          color={i < 3 ? 'red' : i < 10 ? 'orange' : 'grey'}
                          isCompact
                        >
                          {v.count}
                        </Label>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <Title headingLevel="h3" size="lg">Remediation Rates</Title>
          </CardHeader>
          <CardBody style={{ paddingInline: 0 }}>
            {remediationRates.length === 0 ? (
              <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>
                No remediation data yet. Run remediate operations to collect statistics.
              </div>
            ) : (
              <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid" style={{ tableLayout: 'auto' }}>
                <thead>
                  <tr role="row">
                    <th role="columnheader" style={{ paddingLeft: 24 }}>Rule ID</th>
                    <th role="columnheader" style={{ paddingRight: 24, textAlign: 'right' }}>Fixes</th>
                  </tr>
                </thead>
                <tbody>
                  {remediationRates.map((r, i) => (
                    <tr key={r.rule_id} role="row">
                      <td role="cell" style={{ paddingLeft: 24 }}>
                        <span style={{ fontFamily: 'var(--pf-t--global--font--family--mono)' }}>
                          {r.rule_id}
                        </span>
                      </td>
                      <td role="cell" style={{ paddingRight: 24, textAlign: 'right' }}>
                        <Label
                          color={i < 3 ? 'green' : i < 10 ? 'blue' : 'grey'}
                          isCompact
                        >
                          {r.fix_count}
                        </Label>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardBody>
        </Card>

        <Card style={{ gridColumn: '1 / -1' }}>
          <CardHeader>
            <Title headingLevel="h3" size="lg">AI Proposal Acceptance</Title>
          </CardHeader>
          <CardBody style={{ paddingInline: 0 }}>
            {aiAcceptance.length === 0 ? (
              <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>
                No AI proposal data yet. Run remediate operations with AI enabled to collect statistics.
              </div>
            ) : (
              <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid" style={{ tableLayout: 'auto' }}>
                <thead>
                  <tr role="row">
                    <th role="columnheader" style={{ paddingLeft: 24 }}>Rule ID</th>
                    <th role="columnheader" style={{ textAlign: 'right' }}>Approved</th>
                    <th role="columnheader" style={{ textAlign: 'right' }}>Rejected</th>
                    <th role="columnheader" style={{ textAlign: 'right' }}>Pending</th>
                    <th role="columnheader" style={{ textAlign: 'right' }}>Acceptance Rate</th>
                    <th role="columnheader" style={{ paddingRight: 24, textAlign: 'right' }}>Avg Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {aiAcceptance.map((a) => {
                    const total = a.approved + a.rejected;
                    const acceptanceRate = total > 0 ? Math.round((a.approved / total) * 100) : 0;
                    return (
                      <tr key={a.rule_id} role="row">
                        <td role="cell" style={{ paddingLeft: 24 }}>
                          <span style={{ fontFamily: 'var(--pf-t--global--font--family--mono)' }}>
                            {a.rule_id}
                          </span>
                        </td>
                        <td role="cell" style={{ textAlign: 'right' }}>
                          <Label color="green" isCompact>{a.approved}</Label>
                        </td>
                        <td role="cell" style={{ textAlign: 'right' }}>
                          <Label color="red" isCompact>{a.rejected}</Label>
                        </td>
                        <td role="cell" style={{ textAlign: 'right' }}>
                          <Label color="grey" isCompact>{a.pending}</Label>
                        </td>
                        <td role="cell" style={{ textAlign: 'right' }}>
                          <Label
                            color={acceptanceRate >= 80 ? 'green' : acceptanceRate >= 50 ? 'orange' : 'red'}
                            isCompact
                          >
                            {acceptanceRate}%
                          </Label>
                        </td>
                        <td role="cell" style={{ paddingRight: 24, textAlign: 'right' }}>
                          <span style={{ opacity: 0.8 }}>
                            {(a.avg_confidence * 100).toFixed(1)}%
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </CardBody>
        </Card>
      </div>
    </PageLayout>
  );
}
