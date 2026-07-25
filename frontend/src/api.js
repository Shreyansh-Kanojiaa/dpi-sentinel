const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8420";

async function getJSON(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    // Carry the status so callers can tell "no such certificate" (404) apart
    // from "the aggregator is unreachable", which read identically in a bare
    // message but mean very different things to someone holding a printout.
    const err = new Error(`${path} -> ${res.status}`);
    err.status = res.status;
    throw err;
  }
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
  getWitnesses: () => getJSON("/api/witnesses"),
  getLogSummary: () => getJSON("/api/log/summary"),
  getLog: (limit = 6) => getJSON(`/api/log?limit=${limit}`),
  requestCertificate: (body) => postJSONBody("/api/certificates", body),
  getCertificate: (id) => getJSON(`/api/certificates/${encodeURIComponent(id)}`),
  verifyCertificate: (bundle) => postJSONBody("/api/verify", bundle),
};
