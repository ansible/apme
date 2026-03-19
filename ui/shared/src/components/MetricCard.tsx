import React from 'react';
import {
  Card,
  CardBody,
  Content,
} from '@patternfly/react-core';

interface MetricCardProps {
  title: string;
  value: string | number;
  color?: string;
}

export const MetricCard: React.FC<MetricCardProps> = ({
  title,
  value,
  color,
}) => (
  <Card isPlain style={{ textAlign: 'center', minWidth: 180 }}>
    <CardBody>
      <Content
        component="h1"
        style={{ color: color ?? 'inherit', fontSize: '2rem' }}
      >
        {value}
      </Content>
      <Content component="small">{title}</Content>
    </CardBody>
  </Card>
);
