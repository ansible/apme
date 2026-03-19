import React from 'react';
import { Label } from '@patternfly/react-core';
import type { ScanStatus } from '../api-client/models';
import { STATUS_LABELS } from '../utils';

const PF_COLORS: Record<ScanStatus, 'green' | 'red' | 'blue'> = {
  completed: 'green',
  failed: 'red',
  running: 'blue',
};

interface StatusBadgeProps {
  status: ScanStatus;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => (
  <Label color={PF_COLORS[status]} isCompact>
    {STATUS_LABELS[status]}
  </Label>
);
