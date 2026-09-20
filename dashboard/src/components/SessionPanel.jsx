function formatWhen(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

// Read-only: shows whether a Naukri session is saved and how fresh it is.
// The actual login/capture flow is triggered via a token-secured link
// (emailed / bookmarked separately), never exposed as a public button here,
// since it opens a live remote browser session.
export default function SessionPanel({ session, loading, error }) {
  return (
    <section className="card">
      <h2>Naukri Session</h2>
      {loading && !session && <p className="muted">Loading…</p>}
      {error && <p className="error">Could not load session status.</p>}
      {session && (
        <>
          <div className="status-row">
            <span
              className={`dot ${session.session_exists ? "dot-green" : "dot-orange"}`}
            />
            <span>{session.session_exists ? "Session saved" : "No session saved yet"}</span>
          </div>
          {session.login_capture_running && (
            <div className="status-row">
              <span className="dot dot-orange" />
              <span>Login capture in progress…</span>
            </div>
          )}
          <div className="status-meta">
            <p>
              <b>Last updated:</b> {formatWhen(session.last_updated)}
            </p>
          </div>
        </>
      )}
    </section>
  );
}
