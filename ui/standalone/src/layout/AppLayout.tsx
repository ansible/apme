import React from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Content,
  Flex,
  FlexItem,
  Masthead,
  MastheadBrand,
  MastheadContent,
  MastheadMain,
  Nav,
  NavItem,
  NavList,
  Page,
  PageSidebar,
  PageSidebarBody,
  ToggleGroup,
  ToggleGroupItem,
} from '@patternfly/react-core';
import { useTheme } from '../theme/useTheme';

const NAV_ITEMS = [
  { path: '/', label: 'Dashboard' },
  { path: '/scans', label: 'Scans' },
  { path: '/rules', label: 'Rules' },
  { path: '/metrics', label: 'ROI Metrics' },
  { path: '/remediation', label: 'Remediation' },
  { path: '/health', label: 'Health' },
  { path: '/settings', label: 'Settings' },
];

export const AppLayout: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { mode, setMode } = useTheme();

  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/';
    return location.pathname.startsWith(path);
  };

  const header = (
    <Masthead>
      <MastheadMain>
        <MastheadBrand>
          <Content component="h3" style={{ color: '#fff', margin: 0 }}>
            APME
          </Content>
        </MastheadBrand>
      </MastheadMain>
      <MastheadContent>
        <Flex
          justifyContent={{ default: 'justifyContentSpaceBetween' }}
          alignItems={{ default: 'alignItemsCenter' }}
          flexWrap={{ default: 'nowrap' }}
          style={{ width: '100%' }}
        >
          <FlexItem>
            <Content component="small" style={{ color: '#8a8d90' }}>
              Ansible Policy & Modernization Engine
            </Content>
          </FlexItem>
          <FlexItem>
            <ToggleGroup aria-label="Theme switcher" isCompact>
              <ToggleGroupItem
                text="Light"
                isSelected={mode === 'light'}
                onChange={() => setMode('light')}
              />
              <ToggleGroupItem
                text="System"
                isSelected={mode === 'system'}
                onChange={() => setMode('system')}
              />
              <ToggleGroupItem
                text="Dark"
                isSelected={mode === 'dark'}
                onChange={() => setMode('dark')}
              />
            </ToggleGroup>
          </FlexItem>
        </Flex>
      </MastheadContent>
    </Masthead>
  );

  const sidebar = (
    <PageSidebar>
      <PageSidebarBody>
        <Nav>
          <NavList>
            {NAV_ITEMS.map(({ path, label }) => (
              <NavItem
                key={path}
                isActive={isActive(path)}
                onClick={() => navigate(path)}
              >
                {label}
              </NavItem>
            ))}
          </NavList>
        </Nav>
      </PageSidebarBody>
    </PageSidebar>
  );

  return (
    <Page masthead={header} sidebar={sidebar}>
      <Outlet />
    </Page>
  );
};
