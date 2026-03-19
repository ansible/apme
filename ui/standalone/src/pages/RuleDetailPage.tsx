import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Breadcrumb,
  BreadcrumbItem,
  Card,
  CardBody,
  CardTitle,
  CodeBlock,
  CodeBlockCode,
  Content,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  Flex,
  FlexItem,
  Label,
  PageSection,
} from '@patternfly/react-core';
import { useRule, ruleCategory } from '@apme/ui-shared';

export const RuleDetailPage: React.FC = () => {
  const { ruleId } = useParams<{ ruleId: string }>();
  const navigate = useNavigate();
  const { data: rule, isLoading } = useRule(ruleId);

  if (isLoading) {
    return (
      <PageSection>
        <Content component="p">Loading...</Content>
      </PageSection>
    );
  }

  if (!rule) {
    return (
      <PageSection>
        <Content component="p">Rule not found.</Content>
      </PageSection>
    );
  }

  return (
    <PageSection>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
        <FlexItem>
          <Breadcrumb>
            <BreadcrumbItem onClick={() => navigate('/rules')}>
              Rule Catalog
            </BreadcrumbItem>
            <BreadcrumbItem isActive>{rule.rule_id}</BreadcrumbItem>
          </Breadcrumb>
        </FlexItem>

        <FlexItem>
          <Content component="h1">
            <span className="apme-mono">{rule.rule_id}</span>
          </Content>
          <Content component="p">{rule.description}</Content>
        </FlexItem>

        <FlexItem>
          <DescriptionList isHorizontal>
            <DescriptionListGroup>
              <DescriptionListTerm>Validator</DescriptionListTerm>
              <DescriptionListDescription>{rule.validator}</DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Category</DescriptionListTerm>
              <DescriptionListDescription>
                <Label isCompact>{ruleCategory(rule.rule_id)}</Label>
              </DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Fixer</DescriptionListTerm>
              <DescriptionListDescription>
                {rule.has_fixer ? (
                  <Label color="green" isCompact>Deterministic fixer available</Label>
                ) : (
                  'None'
                )}
              </DescriptionListDescription>
            </DescriptionListGroup>
          </DescriptionList>
        </FlexItem>

        {Object.entries(rule.examples).map(([label, yaml]) => (
          <FlexItem key={label}>
            <Card isPlain>
              <CardTitle>
                Example: {label}
              </CardTitle>
              <CardBody>
                <CodeBlock>
                  <CodeBlockCode>{yaml}</CodeBlockCode>
                </CodeBlock>
              </CardBody>
            </Card>
          </FlexItem>
        ))}
      </Flex>
    </PageSection>
  );
};
