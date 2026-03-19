import React from 'react';
import {
  Button,
  Content,
  Flex,
  FlexItem,
  Label,
  PageSection,
} from '@patternfly/react-core';
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
    <PageSection>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
        <FlexItem>
          <Content component="h1">Remediation Queue</Content>
          <Content component="small">
            {proposals.length} pending proposal{proposals.length !== 1 ? 's' : ''}
          </Content>
        </FlexItem>

        <FlexItem>
          {isLoading ? (
            <Content component="p">Loading...</Content>
          ) : proposals.length === 0 ? (
            <Content component="p">No pending remediation proposals.</Content>
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
                    <Td dataLabel="Violation">
                      <span className="apme-mono">{p.violation_id.slice(0, 8)}</span>
                    </Td>
                    <Td dataLabel="Tier">
                      <Label isCompact>
                        Tier {p.tier}
                      </Label>
                    </Td>
                    <Td dataLabel="Proposed By">{p.proposed_by ?? '—'}</Td>
                    <Td dataLabel="Diff">
                      {p.diff ? <DiffViewer diff={p.diff} /> : '—'}
                    </Td>
                    <Td dataLabel="Actions">
                      <Flex spaceItems={{ default: 'spaceItemsSm' }}>
                        <FlexItem>
                          <Button
                            variant="primary"
                            size="sm"
                            isLoading={accept.isPending}
                            onClick={() =>
                              accept.mutate({ proposalId: p.id })
                            }
                          >
                            Accept
                          </Button>
                        </FlexItem>
                        <FlexItem>
                          <Button
                            variant="danger"
                            size="sm"
                            isLoading={reject.isPending}
                            onClick={() =>
                              reject.mutate({ proposalId: p.id })
                            }
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
        </FlexItem>
      </Flex>
    </PageSection>
  );
};
