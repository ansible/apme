import React from 'react';
import {
  Content,
  Flex,
  FlexItem,
} from '@patternfly/react-core';
import type { Violation, ViolationLevel } from '../api-client/models';
import { SeverityBadge } from './SeverityBadge';

interface ViolationRowProps {
  violation: Violation;
}

export const ViolationRow: React.FC<ViolationRowProps> = ({ violation }) => (
  <Flex
    alignItems={{ default: 'alignItemsCenter' }}
    style={{
      padding: '8px 16px',
      borderBottom: '1px solid var(--pf-t--global--border--color--default)',
    }}
  >
    <FlexItem>
      <SeverityBadge level={violation.level as ViolationLevel} />
    </FlexItem>
    <FlexItem>
      <Content
        component="small"
        style={{ fontFamily: 'var(--pf-t--global--font--family--mono)' }}
      >
        {violation.rule_id}
      </Content>
    </FlexItem>
    {violation.line && (
      <FlexItem>
        <Content
          component="small"
          style={{ fontFamily: 'var(--pf-t--global--font--family--mono)' }}
        >
          L{violation.line}
          {violation.line_end ? `–${violation.line_end}` : ''}
        </Content>
      </FlexItem>
    )}
    <FlexItem grow={{ default: 'grow' }}>
      <Content component="p">{violation.message}</Content>
    </FlexItem>
  </Flex>
);
