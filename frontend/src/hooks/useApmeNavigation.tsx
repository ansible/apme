import { useMemo } from 'react';
import type { PageNavigationItem } from '@ansible/ansible-ui-framework';
import { AnalyticsPage } from '../pages/AnalyticsPage';
import { ActivityPage } from '../pages/ActivityPage';
import { ActivityDetailPage } from '../pages/ActivityDetailPage';
import { CollectionsPage } from '../pages/CollectionsPage';
import { CollectionDetailPage } from '../pages/CollectionDetailPage';
import { DashboardPage } from '../pages/DashboardPage';
import { HealthPage } from '../pages/HealthPage';
import { PlaygroundPage } from '../pages/PlaygroundPage';
import { ProjectsPage } from '../pages/ProjectsPage';
import { ProjectDetailPage } from '../pages/ProjectDetailPage';
import { PythonPackagesPage } from '../pages/PythonPackagesPage';
import { PythonPackageDetailPage } from '../pages/PythonPackageDetailPage';
import { SettingsPage } from '../pages/SettingsPage';

export function useApmeNavigation(): PageNavigationItem[] {
  return useMemo<PageNavigationItem[]>(
    () => [
      {
        label: 'Overview',
        path: '',
        children: [
          { id: 'dashboard', path: '', label: 'Dashboard', element: <DashboardPage /> },
          { id: 'analytics', path: 'analytics', label: 'Analytics', element: <AnalyticsPage /> },
        ],
      },
      {
        label: 'Projects',
        path: '',
        children: [
          { id: 'projects', path: 'projects', label: 'Projects', element: <ProjectsPage /> },
          { id: 'project-detail', path: 'projects/:projectId', element: <ProjectDetailPage />, hidden: true },
        ],
      },
      {
        label: 'Dependencies',
        path: '',
        children: [
          { id: 'nav-collections', path: 'collections', label: 'Collections', element: <CollectionsPage /> },
          { id: 'nav-collection-detail', path: 'collections/:fqcn', element: <CollectionDetailPage />, hidden: true },
          { id: 'nav-python-packages', path: 'python-packages', label: 'Python Packages', element: <PythonPackagesPage /> },
          { id: 'nav-python-package-detail', path: 'python-packages/:name', element: <PythonPackageDetailPage />, hidden: true },
        ],
      },
      {
        label: 'Operations',
        path: '',
        children: [
          { id: 'playground', path: 'playground', label: 'Playground', element: <PlaygroundPage /> },
          { id: 'activity', path: 'activity', label: 'Activity', element: <ActivityPage /> },
          { id: 'activity-detail', path: 'activity/:activityId', element: <ActivityDetailPage />, hidden: true },
        ],
      },
      {
        label: 'System',
        path: '',
        children: [
          { id: 'health', path: 'health', label: 'Health', element: <HealthPage /> },
          { id: 'settings', path: 'settings', label: 'Settings', element: <SettingsPage /> },
        ],
      },
    ],
    [],
  );
}
