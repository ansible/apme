import React from 'react';
import { Label } from '@patternfly/react-core';
import type { ViolationLevel } from '../api-client/models';
import { SEVERITY_LABELS } from '../utils';

const PF_COLORS: Record<ViolationLevel, 'red' | 'yellow' | 'blue'> = {
  error: 'red',
  warning: 'yellow',
  hint: 'blue',
};

interface SeverityBadgeProps {
  level: ViolationLevel;
}

export const SeverityBadge: React.FC<SeverityBadgeProps> = ({ level }) => (
  <Label color={PF_COLORS[level]} isCompact>
    {SEVERITY_LABELS[level]}
  </Label>
);
