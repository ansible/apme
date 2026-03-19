import React from 'react';
import { Content, Header, Page } from '@backstage/core-components';
import { Button, Flex, FlexItem, Label } from '@patternfly/react-core';
import { Table, Thead, Tr, Th, Tbody, Td } from '@patternfly/react-table';
import {
  useRemediationQueue,
  useAcceptProposal,
  useRejectProposal,
  DiffViewer,
} from '@apme/ui-shared';

export const RemediationQueuePage: React.FC = () => {
  const { data: proposals = [], isLoading } = useRemediationQueue();
  const accept = useAcceptProposal();
  const reject = useRejectProposal();

  return (
    <Page themeId="tool">
      <Header
        title="Remediation Queue"
        subtitle={`${proposals.length} pending proposals`}
      />
      <Content>
        {isLoading ? (
          <p>Loading...</p>
        ) : proposals.length === 0 ? (
          <p>No pending remediation proposals.</p>
        ) : (
          <Table aria-label="Remediation queue" variant="compact">
            <Thead>
              <Tr>
                <Th>Violation</Th>
                <Th>Tier</Th>
                <Th>Proposed By</Th>
                <Th>Diff</Th>
                <Th>Actions</Th>
              </Tr>
            </Thead>
            <Tbody>
              {proposals.map((p) => (
                <Tr key={p.id}>
                  <Td><code>{p.violation_id.slice(0, 8)}</code></Td>
                  <Td><Label isCompact>Tier {p.tier}</Label></Td>
                  <Td>{p.proposed_by ?? '—'}</Td>
                  <Td>{p.diff ? <DiffViewer diff={p.diff} /> : '—'}</Td>
                  <Td>
                    <Flex spaceItems={{ default: 'spaceItemsSm' }}>
                      <FlexItem>
                        <Button
                          variant="primary"
                          isSmall
                          onClick={() => accept.mutate({ proposalId: p.id })}
                        >
                          Accept
                        </Button>
                      </FlexItem>
                      <FlexItem>
                        <Button
                          variant="danger"
                          isSmall
                          onClick={() => reject.mutate({ proposalId: p.id })}
                        >
                          Reject
                        </Button>
                      </FlexItem>
                    </Flex>
                  </Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </Content>
    </Page>
  );
};
