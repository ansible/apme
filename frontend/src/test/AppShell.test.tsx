import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { PageFramework } from '@ansible/ansible-ui-framework';
import { ApmeApiProvider } from '../api/apmeApiAdapter';
import { ApmeAppBody } from '../shell/App';

function renderApp(path = '/', showMasthead = true) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ApmeApiProvider>
        <PageFramework defaultRefreshInterval={30}>
          <ApmeAppBody showMasthead={showMasthead} />
        </PageFramework>
      </ApmeApiProvider>
    </MemoryRouter>,
  );
}

describe('App Shell', () => {
  it('renders APME branding in the masthead', () => {
    renderApp();
    expect(screen.getByText('APME')).toBeInTheDocument();
  });

  it('renders ApmeAppBody under MemoryRouter without BrowserRouter', () => {
    renderApp('/projects');
    expect(screen.getByTestId('page-navigation')).toBeInTheDocument();
  });

  it('can omit masthead for host-shell embedding', () => {
    renderApp('/', false);
    expect(screen.queryByText('APME')).not.toBeInTheDocument();
    expect(screen.getByTestId('page-navigation')).toBeInTheDocument();
  });

  it('renders sidebar navigation groups', () => {
    renderApp();
    const nav = screen.getByTestId('page-navigation');
    expect(nav).toBeInTheDocument();

    for (const group of ['Overview', 'Projects', 'Operations', 'System']) {
      const items = screen.getAllByText(group);
      expect(items.length).toBeGreaterThanOrEqual(1);
    }
  });

  it('renders sidebar navigation items', () => {
    renderApp();

    for (const label of [
      'Dashboard',
      'Projects',
      'Playground',
      'Activity',
      'Health',
      'Settings',
    ]) {
      const items = screen.getAllByText(label);
      expect(items.length).toBeGreaterThanOrEqual(1);
    }
  });

  it('renders the nav toggle button', () => {
    renderApp();
    expect(screen.getByTestId('nav-toggle')).toBeInTheDocument();
  });

  it('renders the theme switcher', () => {
    renderApp();
    const themeBtn =
      screen.queryByTestId('settings-icon') ?? screen.queryByTestId('theme-icon');
    expect(themeBtn).not.toBeNull();
  });

  it('renders the help menu dropdown toggle', () => {
    renderApp();
    expect(document.getElementById('help-menu-menu-toggle')).toBeInTheDocument();
  });
});
