import React from 'react';
import { Routes, Route } from 'react-router-dom';
import { AppLayout } from './layout/AppLayout';
import { DashboardPage } from './pages/DashboardPage';
import { NewScanPage } from './pages/NewScanPage';
import { ScanListPage } from './pages/ScanListPage';
import { ScanDetailPage } from './pages/ScanDetailPage';
import { RuleCatalogPage } from './pages/RuleCatalogPage';
import { RuleDetailPage } from './pages/RuleDetailPage';
import { RoiMetricsPage } from './pages/RoiMetricsPage';
import { RemediationQueuePage } from './pages/RemediationQueuePage';
import { HealthPage } from './pages/HealthPage';
import { SettingsPage } from './pages/SettingsPage';
import { FixJobPage } from './pages/FixJobPage';

export const App: React.FC = () => (
  <Routes>
    <Route element={<AppLayout />}>
      <Route index element={<DashboardPage />} />
      <Route path="scans/new" element={<NewScanPage />} />
      <Route path="scans" element={<ScanListPage />} />
      <Route path="scans/:scanId" element={<ScanDetailPage />} />
      <Route path="rules" element={<RuleCatalogPage />} />
      <Route path="rules/:ruleId" element={<RuleDetailPage />} />
      <Route path="metrics" element={<RoiMetricsPage />} />
      <Route path="remediation" element={<RemediationQueuePage />} />
      <Route path="fix/:jobId" element={<FixJobPage />} />
      <Route path="health" element={<HealthPage />} />
      <Route path="settings" element={<SettingsPage />} />
    </Route>
  </Routes>
);
