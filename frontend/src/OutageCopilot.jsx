import React, { useState } from "react";
import { api } from "./api";

// Rendered only while a rail's quorum status is "degraded" (App.jsx gates
// this). Two jobs: tell an affected citizen what to actually do right now,
// and let them walk away with a signed, independently verifiable Evidence
// Certificate for the confirmed incident window.

function toLocalInputValue(d) {
  // datetime-local wants "YYYY-MM-DDTHH:MM" in local time.
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function downloadBundle(bundle) {
  // This file — raw certificate JSON + signature + aggregator public key —
  // is the actual artifact a citizen forwards to their bank or the RBI
  // ombudsman. Anyone can later validate it at /verify without trusting
  // how it travelled.
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `dpi-sentinel-certificate-${bundle.certificate.certificate_id}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function CertificateResult({ bundle }) {
  const cert = bundle.certificate;
  const q = cert.witness_quorum_snapshot || {};
  const proven = (cert.log_evidence || []).filter((e) => e.status === "proven").length;
  const total = (cert.log_evidence || []).length;
  const fmt = (iso) => (iso ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("en-IN") : null);

  const row = (label, value) => (
    <div className="cert-row">
      <div className="cert-row-label">{label}</div>
      <div className="cert-row-value">{value}</div>
    </div>
  );

  return (
    <div className="cert-result">
      <span className="stamp stamp--green stamp--tilt stamp--in">Certificate issued</span>
      <div style={{ fontSize: 12, color: "var(--ink-soft)", margin: "10px 0 2px", lineHeight: 1.55 }}>
        Signed by the aggregator's Ed25519 identity. Download the bundle below — that file is what you
        forward to your bank or the ombudsman, and anyone can independently verify it on the{" "}
        <a href="#/verify">verify page</a>.
      </div>

      <div className="cert-rows">
        {row("certificate id", <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{cert.certificate_id}</span>)}
        {row("rail", `${cert.rail.name} (${cert.rail.operator})`)}
        {row("incident window", `${fmt(cert.incident_window.started_at)} → ${cert.incident_window.ongoing ? "ongoing" : fmt(cert.incident_window.resolved_at)}`)}
        {row("witness quorum", q.reporting_count != null
          ? `${(q.unhealthy_witness_ids || []).length} of ${q.reporting_count} reporting witnesses (${(q.unhealthy_witness_ids || []).join(", ")}) marked the rail unhealthy`
          : "—")}
        {row("your claimed time", fmt(cert.claimed_timestamp))}
        {row("your transaction ref", cert.claimed_transaction_ref.value
          ? <span>{cert.claimed_transaction_ref.value}{" "}
              <em style={{ color: "var(--rust)", fontStyle: "normal", fontSize: 11 }}>· self-reported, unverified</em></span>
          : <em style={{ color: "var(--stone)" }}>none provided</em>)}
        {row("cryptographic evidence", `${proven} of ${total} incident log entries carry Merkle inclusion proofs to a signed, git-anchored checkpoint${proven < total ? " (the rest await the next checkpoint)" : ""}`)}
        {row("signature", <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5 }}>{bundle.signature.slice(0, 32)}…</span>)}
      </div>

      <div className="cert-disclaimer">{cert.disclaimer}</div>

      <button className="btn-primary" style={{ marginTop: 14 }} onClick={() => downloadBundle(bundle)}>
        Download certificate (JSON)
      </button>
    </div>
  );
}

export default function OutageCopilot({ rail }) {
  const [showForm, setShowForm] = useState(false);
  const [claimedTime, setClaimedTime] = useState(() => toLocalInputValue(new Date()));
  const [txnRef, setTxnRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [bundle, setBundle] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.requestCertificate({
        rail_slug: rail.slug,
        claimed_timestamp: new Date(claimedTime).toISOString(),
        claimed_transaction_ref: txnRef.trim() || null,
      });
      setBundle(result);
      setShowForm(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="copilot" onClick={(e) => e.stopPropagation()}>
      <div className="copilot-title">Outage copilot — {rail.name} is degraded right now</div>

      <div className="copilot-steps">
        <div>
          <strong>Don't retry the payment immediately.</strong> During an infrastructure incident,
          retries pile onto an already struggling system and can leave you with multiple pending
          debits for the same purchase. Wait a few minutes and check this page before trying again.
        </div>
        <div>
          <strong>Check whether the money actually left your account</strong> — open your bank's own
          app or look for the debit SMS, not the UPI app's spinner. A "failed" screen with a real
          debit usually auto-reverses; note the time and transaction reference if it doesn't.
        </div>
        <div className="copilot-scam">
          <strong>Scam warning:</strong> outage windows are prime time for fake "UPI helpline" calls.
          NPCI and your bank will not call you to "reverse a stuck transaction" or ask for your UPI
          PIN, OTP, or a screen-share. If someone calls you about this outage unprompted, hang up.
        </div>
      </div>

      {!bundle && !showForm && (
        <button className="btn-primary" style={{ marginTop: 14 }} onClick={() => setShowForm(true)}>
          Request Evidence Certificate
        </button>
      )}

      {showForm && !bundle && (
        <form onSubmit={submit} className="copilot-form">
          <div className="copilot-form-note">
            A signed, verifiable record that this incident was confirmed by independent witness
            quorum — usable as supporting evidence in a bank dispute or RBI ombudsman complaint.
          </div>
          <label>
            Rail
            <input value={`${rail.name} (${rail.slug})`} disabled />
          </label>
          <label>
            Approximate time your transaction failed
            <input
              type="datetime-local"
              required
              value={claimedTime}
              onChange={(e) => setClaimedTime(e.target.value)}
            />
          </label>
          <label>
            Transaction reference (optional — recorded as self-reported, not verified)
            <input
              value={txnRef}
              onChange={(e) => setTxnRef(e.target.value)}
              placeholder="e.g. UPI ref no. from your app"
            />
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? "Requesting…" : "Issue certificate"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => setShowForm(false)}>Cancel</button>
          </div>
        </form>
      )}

      {error && <div className="copilot-error">{error}</div>}

      {bundle && <CertificateResult bundle={bundle} />}
    </div>
  );
}
