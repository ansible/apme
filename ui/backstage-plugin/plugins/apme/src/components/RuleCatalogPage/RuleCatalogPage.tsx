import React, { useState } from 'react';
import { Content, Header, Page } from '@backstage/core-components';
import {
  Label,
  SearchInput,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from '@patternfly/react-core';
import { Table, Thead, Tr, Th, Tbody, Td } from '@patternfly/react-table';
import { useRules, ruleCategory } from '@apme/ui-shared';

export const RuleCatalogPage: React.FC = () => {
  const [search, setSearch] = useState('');
  const { data: rules = [], isLoading } = useRules();

  const filtered = rules.filter(
    (r) =>
      !search ||
      r.rule_id.toLowerCase().includes(search.toLowerCase()) ||
      r.description.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <Page themeId="tool">
      <Header title="Rule Catalog" subtitle={`${filtered.length} rules`} />
      <Content>
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
          </ToolbarContent>
        </Toolbar>
        {isLoading ? (
          <p>Loading...</p>
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
                <Tr key={rule.rule_id}>
                  <Td><code>{rule.rule_id}</code></Td>
                  <Td><Label isCompact>{ruleCategory(rule.rule_id)}</Label></Td>
                  <Td>{rule.validator}</Td>
                  <Td>{rule.description}</Td>
                  <Td>{rule.has_fixer && <Label color="green" isCompact>Yes</Label>}</Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </Content>
    </Page>
  );
};
