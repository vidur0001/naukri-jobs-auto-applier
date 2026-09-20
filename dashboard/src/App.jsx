import { usePolling } from "./hooks/usePolling.js";
import { fetchStatus, fetchStats, fetchApplications } from "./api.js";
import StatusCard from "./components/StatusCard.jsx";
import StatsPanel from "./components/StatsPanel.jsx";
import ApplicationsTable from "./components/ApplicationsTable.jsx";

export default function App() {
  const status = usePolling(fetchStatus, 10000);
  const stats = usePolling(fetchStats, 10000);
  const applications = usePolling(() => fetchApplications(50), 15000);

  const isLive = !status.error && !stats.error;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">🤖</span>
          <div>
            <h1>Naukri Auto-Applier</h1>
            <p className="subtitle">Live status, stats and application history</p>
          </div>
        </div>
        <div className={`live-pill ${isLive ? "live-on" : "live-off"}`}>
          <span className="pulse-dot" />
          {isLive ? "Live" : "Offline"}
        </div>
      </header>

      <main className="app-grid">
        <StatusCard
          status={status.data}
          loading={status.loading}
          error={status.error}
        />
        <StatsPanel
          stats={stats.data}
          loading={stats.loading}
          error={stats.error}
        />
        <ApplicationsTable
          applications={applications.data?.applications}
          loading={applications.loading}
          error={applications.error}
        />
      </main>

      <footer className="app-footer">
        <span>Naukri Auto-Applier Dashboard</span>
      </footer>
    </div>
  );
}
