import React, { useState } from "react";
import { api } from "./api";

// Certificate verification page (#/verify). Deliberately trusts NOTHING
// about how the certificate file reached this browser — email attachment,
// WhatsApp forward, USB stick — because validity is re-derived from the
// math: the aggregator's signature, the Merkle inclusion proofs, and the
// git-anchored checkpoint roots. Three separate checks, reported
// separately, because they fail for different reasons.

const CHECK_META = {
  signature: {
    title: "Aggregator signature",
    explains: "Was this exact document, byte for byte, signed by the aggregator's Ed25519 identity key?",
  },
  inclusion_proofs: {
    title: "Merkle inclusion proofs",
    explains: "Does each cited log entry, rebuilt from its own content, hash up through its proof path to the cited checkpoint root?",
  },
  checkpoint_anchor: {
    title: "External checkpoint anchor",
    explains: "Do the cited checkpoint roots match the copies committed to the external git repository — not just the aggregator's own database?",
  },
};

function CheckRow({ name, result }) {
  const meta = CHECK_META[name] || { title: name, explains: "" };
  const passed = result.passed;
  const color = passed === true ? "var(--green)" : passed === false ? "var(--rust)" : "var(--amber)";
  const label = passed === true ? "PASS" : passed === false ? "FAIL" : "NOT CHECKED";
  return (
    <div style={{ borderBottom: "1px solid var(--stone-line)", padding: "12px 0" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4 }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 700, color, border: `1px solid ${color}`, padding: "1px 7px" }}>
          {label}
        </span>
        <span style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 600 }}>{meta.title}</span>
      </div>
      <div style={{ fontSize: 12, color: "var(--stone)", marginBottom: 4 }}>{meta.explains}</div>
      <div style={{ fontSize: 12.5, lineHeight: 1.55, color: "var(--ink-soft)" }}>{result.detail}</div>
    </div>
  );
}

export default function VerifyPage() {
  const [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const onFile = async (e) => {
    const f = e.target.files?.[0];
    if (f) setRaw(await f.text());
  };

  const verify = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      let bundle;
      try {
        bundle = JSON.parse(raw);
      } catch {
        throw new Error("That doesn't parse as JSON — paste or upload the exact certificate file that was downloaded.");
      }
      if (!bundle.certificate || !bundle.signature) {
        throw new Error('Expected a bundle with "certificate" and "signature" fields — the file exactly as issued.');
      }
      setResult(await api.verifyCertificate(bundle));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: "48px 28px 96px" }}>
      <header style={{ marginBottom: 28 }}>
        <div style={{ fontSize: 11, letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--stone)", marginBottom: 6 }}>
          DPI Sentinel · <a href="#/">back to status page</a>
        </div>
        <h1 style={{ fontFamily: "var(--font-display)", fontSize: 30, margin: 0, fontWeight: 700 }}>
          Verify an Evidence Certificate
        </h1>
        <div style={{ fontSize: 13.5, color: "var(--ink-soft)", marginTop: 8, lineHeight: 1.6, maxWidth: 620 }}>
          Paste or upload a certificate file, exactly as it was downloaded. It doesn't matter where the
          file came from or who forwarded it — validity is re-derived from the cryptography, not from
          trusting the sender.
        </div>
      </header>

      <textarea
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        placeholder='{"certificate": { … }, "signature": "…", "aggregator_public_key_hex": "…"}'
        spellCheck={false}
        style={{
          width: "100%", minHeight: 180, fontFamily: "var(--font-mono)", fontSize: 11.5,
          padding: 12, border: "1px solid var(--stone-line)", background: "var(--paper-raised)",
          color: "var(--ink)", resize: "vertical",
        }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 10 }}>
        <button className="btn-danger" onClick={verify} disabled={busy || !raw.trim()}>
          {busy ? "Verifying…" : "Verify certificate"}
        </button>
        <label style={{ fontSize: 12.5, color: "var(--ink-soft)", cursor: "pointer" }}>
          …or upload the file: <input type="file" accept=".json,application/json" onChange={onFile} />
        </label>
      </div>

      {error && (
        <div style={{ marginTop: 16, fontSize: 12.5, color: "var(--rust)", border: "1px solid var(--rust)", padding: "10px 12px" }}>
          {error}
        </div>
      )}

      {result && (
        <section style={{ marginTop: 28 }}>
          <div
            style={{
              display: "inline-flex", alignItems: "center", fontSize: 14, fontWeight: 700,
              padding: "8px 16px", marginBottom: 12,
              border: `1px solid ${result.valid ? "var(--green)" : "var(--rust)"}`,
              color: result.valid ? "var(--green)" : "var(--rust)",
              background: result.valid ? "var(--green-dim)" : "var(--rust-dim)",
            }}
          >
            {result.valid
              ? "VALID — every evaluable check passed"
              : `INVALID — failed: ${result.failed_checks.join(", ")}`}
          </div>

          <div>
            {["signature", "inclusion_proofs", "checkpoint_anchor"].map(
              (name) => result.checks[name] && <CheckRow key={name} name={name} result={result.checks[name]} />
            )}
          </div>

          <div style={{ marginTop: 14, fontSize: 11.5, color: "var(--stone)", lineHeight: 1.6 }}>
            Verified against aggregator identity{" "}
            <span style={{ fontFamily: "var(--font-mono)" }}>{result.aggregator_public_key_hex?.slice(0, 16)}…</span>.
            A certificate only ever attests that an infrastructure incident was confirmed by witness
            quorum during its window — never the outcome of any individual transaction.
          </div>
        </section>
      )}
    </div>
  );
}
