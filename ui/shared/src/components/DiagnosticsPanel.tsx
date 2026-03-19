import React from 'react';
import {
  Card,
  CardBody,
  CardTitle,
  Content,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  Flex,
  FlexItem,
} from '@patternfly/react-core';
import type { Diagnostics } from '../api-client/models';
import { formatDuration } from '../utils';

interface DiagnosticsPanelProps {
  diagnostics: Diagnostics;
}

export const DiagnosticsPanel: React.FC<DiagnosticsPanelProps> = ({
  diagnostics,
}) => (
  <Card isPlain>
    <CardTitle>Scan Diagnostics</CardTitle>
    <CardBody>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsMd' }}>
        <FlexItem>
          <DescriptionList isHorizontal isCompact>
            <DescriptionListGroup>
              <DescriptionListTerm>Total time</DescriptionListTerm>
              <DescriptionListDescription>
                {formatDuration(diagnostics.total_ms)}
              </DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Files scanned</DescriptionListTerm>
              <DescriptionListDescription>
                {diagnostics.files_scanned}
              </DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Engine (parse)</DescriptionListTerm>
              <DescriptionListDescription>
                {formatDuration(diagnostics.engine_parse_ms)}
              </DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Engine (annotate)</DescriptionListTerm>
              <DescriptionListDescription>
                {formatDuration(diagnostics.engine_annotate_ms)}
              </DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Fan-out</DescriptionListTerm>
              <DescriptionListDescription>
                {formatDuration(diagnostics.fan_out_ms)}
              </DescriptionListDescription>
            </DescriptionListGroup>
          </DescriptionList>
        </FlexItem>

        {diagnostics.validators.length > 0 && (
          <FlexItem>
            <Content component="h4">Validator breakdown</Content>
            <DescriptionList isHorizontal isCompact>
              {diagnostics.validators.map((v) => (
                <DescriptionListGroup key={v.validator_name}>
                  <DescriptionListTerm>{v.validator_name}</DescriptionListTerm>
                  <DescriptionListDescription>
                    {formatDuration(v.total_ms)} — {v.violations_found} violations
                  </DescriptionListDescription>
                </DescriptionListGroup>
              ))}
            </DescriptionList>
          </FlexItem>
        )}
      </Flex>
    </CardBody>
  </Card>
);
