function StatBlock({ label, value, accent }) {
  return (
    <div className="stat-block">
      <span className={`stat-value ${accent || ""}`}>{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

export default function StatsPanel({ stats, loading, error }) {
  const today = stats?.today_stats || {};

  return (
    <section className="card">
      <h2>Today's Activity {stats?.today ? `(${stats.today})` : ""}</h2>
      {loading && !stats && <p className="muted">Loading…</p>}
      {error && <p className="error">Could not load stats.</p>}
      {stats && (
        <>
          <div className="stats-grid">
            <StatBlock label="Found" value={today.found ?? 0} />
            <StatBlock label="Applied" value={today.applied ?? 0} accent="accent-green" />
            <StatBlock label="Rejected" value={today.rejected ?? 0} accent="accent-red" />
          </div>
          <HistoryTable history={stats.history} />
        </>
      )}
    </section>
  );
}

function HistoryTable({ history }) {
  if (!history) return null;
  const dates = Object.keys(history).sort().reverse().slice(0, 7);
  if (dates.length === 0) return null;

  return (
    <div className="table-scroll">
      <table className="history-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Found</th>
            <th>Applied</th>
            <th>Rejected</th>
          </tr>
        </thead>
        <tbody>
          {dates.map((d) => (
            <tr key={d}>
              <td>{d}</td>
              <td>{history[d].found ?? 0}</td>
              <td>{history[d].applied ?? 0}</td>
              <td>{history[d].rejected ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
