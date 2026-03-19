import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Content,
  Flex,
  FlexItem,
  Label,
  PageSection,
  SearchInput,
  ToggleGroup,
  ToggleGroupItem,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from '@patternfly/react-core';
import { Table, Thead, Tr, Th, Tbody, Td } from '@patternfly/react-table';
import { useRules, ruleCategory } from '@apme/ui-shared';

const VALIDATORS = ['all', 'opa', 'native', 'ansible', 'gitleaks'] as const;

export const RuleCatalogPage: React.FC = () => {
  const navigate = useNavigate();
  const [validator, setValidator] = useState<string>('all');
  const [search, setSearch] = useState('');

  const { data: rules = [], isLoading } = useRules(
    validator === 'all' ? undefined : { validator },
  );

  const filtered = rules.filter(
    (r) =>
      !search ||
      r.rule_id.toLowerCase().includes(search.toLowerCase()) ||
      r.description.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <PageSection>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
        <FlexItem>
          <Content component="h1">Rule Catalog</Content>
          <Content component="small">{filtered.length} rules</Content>
        </FlexItem>

        <FlexItem>
          <Toolbar>
            <ToolbarContent>
              <ToolbarItem>
                <SearchInput
                  placeholder="Search rules..."
                  value={search}
                  onChange={(_e, val) => setSearch(val)}
                  onClear={() => setSearch('')}
                />
              </ToolbarItem>
              <ToolbarItem>
                <ToggleGroup>
                  {VALIDATORS.map((v) => (
                    <ToggleGroupItem
                      key={v}
                      text={v.charAt(0).toUpperCase() + v.slice(1)}
                      isSelected={validator === v}
                      onChange={() => setValidator(v)}
                    />
                  ))}
                </ToggleGroup>
              </ToolbarItem>
            </ToolbarContent>
          </Toolbar>
        </FlexItem>

        <FlexItem>
          {isLoading ? (
            <Content component="p">Loading...</Content>
          ) : (
            <Table aria-label="Rule catalog" variant="compact">
              <Thead>
                <Tr>
                  <Th>Rule ID</Th>
                  <Th>Category</Th>
                  <Th>Validator</Th>
                  <Th>Description</Th>
                  <Th>Fixer</Th>
                </Tr>
              </Thead>
              <Tbody>
                {filtered.map((rule) => (
                  <Tr
                    key={rule.rule_id}
                    isClickable
                    onRowClick={() => navigate(`/rules/${rule.rule_id}`)}
                  >
                    <Td dataLabel="Rule ID">
                      <span className="apme-mono">{rule.rule_id}</span>
                    </Td>
                    <Td dataLabel="Category">
                      <Label isCompact>{ruleCategory(rule.rule_id)}</Label>
                    </Td>
                    <Td dataLabel="Validator">{rule.validator}</Td>
                    <Td dataLabel="Description">{rule.description}</Td>
                    <Td dataLabel="Fixer">
                      {rule.has_fixer && <Label color="green" isCompact>Yes</Label>}
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          )}
        </FlexItem>
      </Flex>
    </PageSection>
  );
};
