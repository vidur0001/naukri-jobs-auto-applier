// Thin client for the Flask backend's read-only JSON API (app.py: /api/*).
// Base URL is configurable at build time via VITE_API_BASE_URL, for cases
// where the dashboard is hosted separately from the Flask API. If unset,
// default to same-origin in production (Flask serves this build itself, so
// relative paths like "/api/status" just work - no CORS needed) and to
// localhost:5000 in dev (Vite's dev server runs on a different port/origin
// than Flask). Note: on Windows, `$env:VITE_API_BASE_URL=""` unsets the var
// entirely rather than setting it to an empty string, so don't rely on an
// explicit "" override - just leave it unset for same-origin production builds.
const BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.DEV ? "http://localhost:5000" : "");

async function getJSON(path) {
  const res = await fetch(`${BASE_URL}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status}`);
  }
  return res.json();
}

export function fetchStatus() {
  return getJSON("/api/status");
}

export function fetchStats() {
  return getJSON("/api/stats");
}

export function fetchApplications(limit = 50) {
  return getJSON(`/api/applications?limit=${limit}`);
}

export function fetchSessionStatus() {
  return getJSON("/api/session-status");
}
