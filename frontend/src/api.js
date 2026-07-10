const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8420";

async function getJSON(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function postJSON(path) {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function postJSONBody(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    // Surface the backend's own message (e.g. the 404 "no quorum-confirmed
    // incident" explanation or the 429 rate-limit text), not just a code.
    throw new Error(data?.detail || `${path} -> ${res.status}`);
  }
  return data;
}

export const api = {
  getRails: () => getJSON("/api/rails"),
  getRail: (slug) => getJSON(`/api/rails/${slug}`),
  getIncidents: (railSlug) =>
    getJSON(railSlug ? `/api/incidents?rail=${railSlug}` : "/api/incidents"),
  getMethodology: () => getJSON("/api/methodology"),
  triggerOutage: (slug, severity = 0.4) =>
    postJSON(`/api/demo/trigger-outage/${slug}?severity=${severity}`),
  resolveOutage: (slug) => postJSON(`/api/demo/resolve-outage/${slug}`),
  requestCertificate: (body) => postJSONBody("/api/certificates", body),
  verifyCertificate: (bundle) => postJSONBody("/api/verify", bundle),
};
