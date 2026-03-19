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
  PageSection,
} from '@patternfly/react-core';

export const SettingsPage: React.FC = () => {
  const gatewayUrl = window.location.origin;

  return (
    <PageSection>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
        <FlexItem>
          <Content component="h1">Settings</Content>
        </FlexItem>

        <FlexItem>
          <Card isPlain>
            <CardTitle>Connection</CardTitle>
            <CardBody>
              <DescriptionList isHorizontal>
                <DescriptionListGroup>
                  <DescriptionListTerm>Gateway URL</DescriptionListTerm>
                  <DescriptionListDescription>
                    <span className="apme-mono">{gatewayUrl}</span>
                  </DescriptionListDescription>
                </DescriptionListGroup>
                <DescriptionListGroup>
                  <DescriptionListTerm>API Docs</DescriptionListTerm>
                  <DescriptionListDescription>
                    <a
                      href={`${gatewayUrl}/api/v1/docs`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {gatewayUrl}/api/v1/docs
                    </a>
                  </DescriptionListDescription>
                </DescriptionListGroup>
                <DescriptionListGroup>
                  <DescriptionListTerm>OpenAPI Spec</DescriptionListTerm>
                  <DescriptionListDescription>
                    <a
                      href={`${gatewayUrl}/api/v1/openapi.json`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {gatewayUrl}/api/v1/openapi.json
                    </a>
                  </DescriptionListDescription>
                </DescriptionListGroup>
              </DescriptionList>
            </CardBody>
          </Card>
        </FlexItem>

        <FlexItem>
          <Card isPlain>
            <CardTitle>About</CardTitle>
            <CardBody>
              <DescriptionList isHorizontal>
                <DescriptionListGroup>
                  <DescriptionListTerm>Application</DescriptionListTerm>
                  <DescriptionListDescription>
                    APME — Ansible Policy & Modernization Engine
                  </DescriptionListDescription>
                </DescriptionListGroup>
                <DescriptionListGroup>
                  <DescriptionListTerm>Version</DescriptionListTerm>
                  <DescriptionListDescription>0.1.0</DescriptionListDescription>
                </DescriptionListGroup>
              </DescriptionList>
            </CardBody>
          </Card>
        </FlexItem>
      </Flex>
    </PageSection>
  );
};
