export default function StatusCard({ status, loading, error }) {
  return (
    <section className="card status-card">
      <h2>Bot Status</h2>
      {loading && !status && <p className="muted">Loading…</p>}
      {error && <p className="error">Could not reach bot server.</p>}
      {status && (
        <>
          <div className="status-row">
            <span
              className={`dot ${status.bot_running ? "dot-green" : "dot-gray"}`}
            />
            <span>{status.bot_running ? "Running" : "Stopped"}</span>
          </div>
          <div className="status-row">
            <span
              className={`dot ${status.auto_apply_paused ? "dot-orange" : "dot-green"}`}
            />
            <span>
              Auto-apply {status.auto_apply_paused ? "Paused" : "Active"}
            </span>
          </div>
          <div className="status-meta">
            <p>
              <b>Roles:</b> {status.target_roles?.join(", ") || "—"}
            </p>
            <p>
              <b>Locations:</b> {status.locations?.join(", ") || "—"}
            </p>
          </div>
        </>
      )}
    </section>
  );
}
