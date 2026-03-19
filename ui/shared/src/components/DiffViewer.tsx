import React from 'react';
import { CodeBlock, CodeBlockCode } from '@patternfly/react-core';

interface DiffViewerProps {
  diff: string;
  title?: string;
}

export const DiffViewer: React.FC<DiffViewerProps> = ({ diff, title }) => (
  <div>
    {title && (
      <div
        style={{
          padding: '8px 12px',
          fontFamily: 'var(--pf-t--global--font--family--mono)',
          fontSize: '0.85rem',
          borderBottom: '1px solid var(--pf-t--global--border--color--default)',
        }}
      >
        {title}
      </div>
    )}
    <CodeBlock>
      <CodeBlockCode>
        {diff}
      </CodeBlockCode>
    </CodeBlock>
  </div>
);
