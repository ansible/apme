import React from 'react';
import { Content as BsContent, Header, Page } from '@backstage/core-components';
import {
  Card,
  CardBody,
  CardTitle,
  Content,
  Flex,
  FlexItem,
  Gallery,
  Label,
} from '@patternfly/react-core';
import { useHealth, formatDuration } from '@apme/ui-shared';

export const HealthPage: React.FC = () => {
  const { data, isLoading } = useHealth();

  return (
    <Page themeId="tool">
      <Header
        title="Service Health"
        subtitle={data ? `Overall: ${data.status.toUpperCase()}` : 'Checking...'}
      />
      <BsContent>
        {isLoading ? (
          <p>Checking services...</p>
        ) : (
          <Gallery hasGutter minWidths={{ default: '240px' }}>
            {data?.services.map((svc) => {
              const isOk = svc.status === 'ok';
              return (
                <Card key={svc.name} isPlain>
                  <CardTitle>
                    <Flex justifyContent={{ default: 'justifyContentSpaceBetween' }}>
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
                      {svc.latency_ms != null
                        ? `Latency: ${formatDuration(svc.latency_ms)}`
                        : 'N/A'}
                    </Content>
                  </CardBody>
                </Card>
              );
            })}
          </Gallery>
        )}
      </BsContent>
    </Page>
  );
};
