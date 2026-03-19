/**
 * Entity overview card for Backstage catalog items.
 *
 * Shows the latest scan summary for a catalog entity (component/service).
 * The entity's metadata.annotations['apme.io/project-name'] is used
 * to look up the most recent scan.
 */

import React from 'react';
import { useEntity } from '@backstage/plugin-catalog-react';
import { InfoCard } from '@backstage/core-components';
import {
  Content,
  Flex,
  FlexItem,
} from '@patternfly/react-core';
import {
  useScans,
  StatusBadge,
  SEVERITY_COLORS,
  formatRelativeTime,
  type ScanStatus,
} from '@apme/ui-shared';

const ANNOTATION = 'apme.io/project-name';

export const EntityApmeCard: React.FC = () => {
  const { entity } = useEntity();
  const projectName =
    entity.metadata.annotations?.[ANNOTATION] ??
    entity.metadata.name;

  const { data, isLoading } = useScans({
    page: 1,
    page_size: 1,
    project: projectName,
  });

  const latest = data?.items?.[0];

  return (
    <InfoCard title="APME Scan" subheader={projectName}>
      {isLoading ? (
        <p>Loading...</p>
      ) : !latest ? (
        <Content component="p">No scans found for this component.</Content>
      ) : (
        <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsSm' }}>
          <FlexItem>
            <Flex spaceItems={{ default: 'spaceItemsMd' }}>
              <FlexItem>
                <StatusBadge status={latest.status as ScanStatus} />
              </FlexItem>
              <FlexItem>
                <Content component="small">
                  {formatRelativeTime(latest.created_at)}
                </Content>
              </FlexItem>
            </Flex>
          </FlexItem>
          <FlexItem>
            <Flex spaceItems={{ default: 'spaceItemsLg' }}>
              <FlexItem>
                <span style={{ color: SEVERITY_COLORS.error }}>{latest.error_count} errors</span>
              </FlexItem>
              <FlexItem>
                <span style={{ color: SEVERITY_COLORS.warning }}>{latest.warning_count} warnings</span>
              </FlexItem>
              <FlexItem>
                <span style={{ color: SEVERITY_COLORS.hint }}>{latest.hint_count} hints</span>
              </FlexItem>
            </Flex>
          </FlexItem>
        </Flex>
      )}
    </InfoCard>
  );
};
