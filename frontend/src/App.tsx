import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { DashboardPage } from "./pages/DashboardPage";
import { ScanDetailPage } from "./pages/ScanDetailPage";
import { ScansPage } from "./pages/ScansPage";
import { TopViolationsPage } from "./pages/TopViolationsPage";
import { FixTrackerPage } from "./pages/FixTrackerPage";
import { AiMetricsPage } from "./pages/AiMetricsPage";
import { HealthPage } from "./pages/HealthPage";

export function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/scans" element={<ScansPage />} />
          <Route path="/scans/:scanId" element={<ScanDetailPage />} />
          <Route path="/violations" element={<TopViolationsPage />} />
          <Route path="/fix-tracker" element={<FixTrackerPage />} />
          <Route path="/ai-metrics" element={<AiMetricsPage />} />
          <Route path="/health" element={<HealthPage />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
