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
  const stampCls = passed === true ? "stamp--green" : passed === false ? "stamp--rust" : "stamp--amber";
  const label = passed === true ? "Pass" : passed === false ? "Fail" : "Not checked";
  return (
    <div className="check-row">
      <div className="check-row-head">
        <span className={`stamp ${stampCls}`}>{label}</span>
        <span className="check-title">{meta.title}</span>
      </div>
      <div className="check-explains">{meta.explains}</div>
      <div className="check-detail">{result.detail}</div>
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
    <div className="page page--narrow">
      <header style={{ marginBottom: 28 }}>
        <div className="eyebrow">
          DPI Sentinel · <a href="#/">back to status page</a>
        </div>
        <h1 className="masthead-title" style={{ fontSize: 32 }}>
          Verify an Evidence Certificate
        </h1>
        <div className="masthead-sub" style={{ maxWidth: 620 }}>
          Paste or upload a certificate file, exactly as it was downloaded. It doesn't matter where the
          file came from or who forwarded it — validity is re-derived from the cryptography, not from
          trusting the sender.
        </div>
      </header>

      <textarea
        className="verify-input"
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        placeholder='{"certificate": { … }, "signature": "…", "aggregator_public_key_hex": "…"}'
        spellCheck={false}
      />
      <div className="verify-actions">
        <button className="btn-primary" onClick={verify} disabled={busy || !raw.trim()}>
          {busy ? "Verifying…" : "Verify certificate"}
        </button>
        <label className="verify-upload">
          …or upload the file: <input type="file" accept=".json,application/json" onChange={onFile} />
        </label>
      </div>

      {error && <div className="copilot-error">{error}</div>}

      {result && (
        <section style={{ marginTop: 28 }}>
          <div className={`verdict-banner ${result.valid ? "verdict-banner--valid" : "verdict-banner--invalid"}`}>
            {result.valid
              ? "VALID — every evaluable check passed"
              : `INVALID — failed: ${result.failed_checks.join(", ")}`}
          </div>

          <div>
            {["signature", "inclusion_proofs", "checkpoint_anchor"].map(
              (name) => result.checks[name] && <CheckRow key={name} name={name} result={result.checks[name]} />
            )}
          </div>

          <div style={{ marginTop: 16, fontSize: 11.5, color: "var(--stone)", lineHeight: 1.6 }}>
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
