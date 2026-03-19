import React from 'react';
import {
  Card,
  CardBody,
  CardTitle,
  Content,
  Flex,
  FlexItem,
  Gallery,
  Label,
  PageSection,
} from '@patternfly/react-core';
import { useHealth, formatDuration } from '@apme/ui-shared';

export const HealthPage: React.FC = () => {
  const { data, isLoading } = useHealth();

  return (
    <PageSection>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
        <FlexItem>
          <Flex alignItems={{ default: 'alignItemsCenter' }} spaceItems={{ default: 'spaceItemsMd' }}>
            <FlexItem>
              <Content component="h1">Service Health</Content>
            </FlexItem>
            {data && (
              <FlexItem>
                <Label color={data.status === 'ok' ? 'green' : 'orange'}>
                  {data.status.toUpperCase()}
                </Label>
              </FlexItem>
            )}
          </Flex>
        </FlexItem>

        <FlexItem>
          {isLoading ? (
            <Content component="p">Checking services...</Content>
          ) : (
            <Gallery hasGutter minWidths={{ default: '240px' }}>
              {data?.services.map((svc) => {
                const isOk = svc.status.startsWith('ok');
                return (
                  <Card key={svc.name} isPlain>
                    <CardTitle>
                      <Flex
                        justifyContent={{ default: 'justifyContentSpaceBetween' }}
                        alignItems={{ default: 'alignItemsCenter' }}
                      >
                        <FlexItem>{svc.name}</FlexItem>
                        <FlexItem>
                          <Label color={isOk ? 'green' : 'red'} isCompact>
                            {isOk ? 'OK' : 'ERROR'}
                          </Label>
                        </FlexItem>
                      </Flex>
                    </CardTitle>
                    <CardBody>
                      <Content component="small">
                        Status: {svc.status}
                      </Content>
                      {svc.latency_ms != null && (
                        <Content component="small">
                          Latency: {formatDuration(svc.latency_ms)}
                        </Content>
                      )}
                    </CardBody>
                  </Card>
                );
              })}
            </Gallery>
          )}
        </FlexItem>
      </Flex>
    </PageSection>
  );
};
