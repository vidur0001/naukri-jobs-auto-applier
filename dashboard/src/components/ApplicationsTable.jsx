const STATUS_LABELS = {
  applied: { label: "Applied", cls: "badge-green" },
  skipped_by_user: { label: "Skipped", cls: "badge-gray" },
  ai_skip: { label: "AI Skip", cls: "badge-gray" },
  rejected_external_site: { label: "External Site", cls: "badge-orange" },
  rejected_no_apply_button: { label: "No Apply Button", cls: "badge-orange" },
  error: { label: "Error", cls: "badge-red" },
};

function formatTimestamp(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

export default function ApplicationsTable({ applications, loading, error }) {
  return (
    <section className="card applications-card">
      <h2>Recent Applications</h2>
      {loading && !applications && <p className="muted">Loading…</p>}
      {error && <p className="error">Could not load applications.</p>}
      {applications && applications.length === 0 && (
        <p className="muted">No applications recorded yet.</p>
      )}
      {applications && applications.length > 0 && (
        <div className="table-scroll">
          <table className="applications-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Company</th>
                <th>Location</th>
                <th>Status</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {applications.map((app, idx) => {
                const meta = STATUS_LABELS[app.status] || {
                  label: app.status,
                  cls: "badge-gray",
                };
                return (
                  <tr key={`${app.link}-${app.timestamp}-${idx}`}>
                    <td>
                      <a href={app.link} target="_blank" rel="noreferrer">
                        {app.title || "Untitled"}
                      </a>
                    </td>
                    <td>{app.company || "—"}</td>
                    <td>{app.location || "—"}</td>
                    <td>
                      <span className={`badge ${meta.cls}`}>{meta.label}</span>
                    </td>
                    <td>{formatTimestamp(app.timestamp)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
