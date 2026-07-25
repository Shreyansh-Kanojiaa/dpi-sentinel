import React, { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { api } from "./api";

// Printable Evidence Certificate (#/certificate/<id>).
//
// This page is a HUMAN-READABLE COPY. The verifiable artifact is the signed
// JSON bundle; this sheet of paper only reproduces it for reading and points
// at where it can be checked.
//
// HONESTY RULE, and the easiest one to lose while styling this: the print
// view says "Certificate issued", never "VALID". It fetched a document; it
// did not verify one. Verdicts belong on #/verify and only after the three
// checks have actually run. Printing "VALID" on paper that was never checked
// is exactly the unearned reassurance this project argues against.

const FP_PREFIX = 16;   // 64 bits: plenty to pin a document, keeps the QR small

// Deriving from window.location means zero config in production. The override
// exists for one specific, embarrassing failure: a certificate printed from a
// demo laptop encodes "localhost:5173", which no phone can resolve. Set
// VITE_PUBLIC_BASE_URL when the page will be printed for someone else.
// Not exported: the rendered URL is asserted via the data-verify-url
// attribute on the QR wrapper, which tests the value actually printed rather
// than a function that might drift from it.
function verifyUrl(base, id, fingerprint) {
  return `${base}#/verify/${id}?f=${(fingerprint || "").slice(0, FP_PREFIX)}`;
}

function publicBase() {
  return (
    import.meta.env.VITE_PUBLIC_BASE_URL ||
    `${window.location.origin}${window.location.pathname}`
  );
}

// Backend timestamps are naive UTC with no trailing Z; without this every
// time on the printed page renders an hour or more out.
const fmt = (iso) => (iso ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("en-IN") : null);

function Row({ label, children }) {
  return (
    <div className="cert-row">
      <div className="cert-row-label">{label}</div>
      <div className="cert-row-value">{children}</div>
    </div>
  );
}

function prettyPayload(payload) {
  // payload is already a JSON *string*. A malformed one must not blank the
  // page, so fall back to printing it raw.
  try {
    return JSON.stringify(JSON.parse(payload), null, 2);
  } catch {
    return payload;
  }
}

function EvidenceEntry({ entry }) {
  return (
    <div className="cert-appendix-entry">
      <div className="cert-appendix-head">
        entry #{entry.sequence_number} · {entry.entry_type} · {entry.status}
      </div>
      <div className="cert-hash-pair">
        <div><span>prev_hash</span> {entry.prev_hash}</div>
        <div><span>entry_hash</span> {entry.entry_hash}</div>
      </div>
      <pre className="cert-pre">{prettyPayload(entry.payload)}</pre>

      {entry.status === "proven" ? (
        <>
          <div className="cert-appendix-sub">Merkle inclusion proof</div>
          <ol className="cert-proof">
            {(entry.proof || []).map((step, i) => (
              <li key={i}>
                <span>{step.position}</span> {step.sibling}
              </li>
            ))}
          </ol>
          <div className="cert-appendix-sub">Checkpoint</div>
          <div className="cert-hash-pair">
            <div><span>entries</span> {entry.checkpoint.seq_start} to {entry.checkpoint.seq_end}</div>
            <div><span>merkle_root</span> {entry.checkpoint.merkle_root}</div>
            <div><span>signature</span> {entry.checkpoint.aggregator_signature}</div>
          </div>
        </>
      ) : (
        // The data already takes this stance; the paper must not quietly hide
        // it behind an empty proof block.
        <div className="cert-appendix-note">
          Not yet covered by a published checkpoint when this certificate was issued. This entry's
          inclusion cannot be proven from this document alone.
        </div>
      )}
    </div>
  );
}

export default function CertificatePrint({ id }) {
  const [bundle, setBundle] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getCertificate(id).then(setBundle).catch((e) => setError(e.message));
  }, [id]);

  if (error) {
    return (
      <div className="page page--narrow">
        <div className="eyebrow" style={{ marginBottom: 14 }}>DPI Sentinel</div>
        <a className="btn-ghost no-print" href="#/">← Back to status page</a>
        <h1 className="masthead-title" style={{ fontSize: 28, marginTop: 18 }}>
          Certificate not found
        </h1>
        <div className="copilot-error" style={{ marginTop: 12 }}>{error}</div>
        <p style={{ fontSize: 13, color: "var(--ink-soft)", maxWidth: 620 }}>
          If you still have the certificate file, you can check it directly on the{" "}
          <a href="#/verify">verify page</a> without needing this id.
        </p>
      </div>
    );
  }

  if (!bundle) {
    return <div className="page page--narrow"><p style={{ color: "var(--stone)" }}>Loading certificate…</p></div>;
  }

  const cert = bundle.certificate;
  const q = cert.witness_quorum_snapshot || {};
  const evidence = cert.log_evidence || [];
  const proven = evidence.filter((e) => e.status === "proven").length;
  const url = verifyUrl(publicBase(), cert.certificate_id, bundle.fingerprint);

  const print = () => {
    // Chrome uses document.title as the default "Save as PDF" filename. One
    // line between "DPI Sentinel.pdf" and something a clerk can file.
    document.title = `dpi-sentinel-certificate-${cert.certificate_id}`;
    window.print();
  };

  return (
    <div className="page page--narrow cert-print">
      <div className="no-print cert-print-controls">
        <a className="btn-ghost" href="#/">← Back to status page</a>
        <button className="btn-primary" onClick={print}>Print / Save as PDF</button>
        <p className="cert-print-hint">
          Use your browser's print dialog and choose "Save as PDF". This document is designed to
          print correctly in black and white, so there is no need to enable background graphics.
        </p>
      </div>

      {/* ---------- The document: exactly one printed page ----------
          Page count is deliberately independent of the incident's size. The
          appendix below is screen-only, so a flapping incident with twenty
          log entries prints the same single sheet as a quiet one. */}
      <section className="cert-doc">
        <header className="cert-letterhead">
          <div>
            <div className="cert-issuer">DPI Sentinel</div>
            <div className="cert-issuer-sub">
              Independent infrastructure monitor · not affiliated with NPCI, MeitY or UIDAI
            </div>
          </div>
          {/* Never a verdict. This sheet was fetched, not verified. */}
          <span className="stamp stamp--green">Certificate issued</span>
        </header>

        <div className="cert-titleblock">
          <h1 className="cert-doc-title">Evidence Certificate</h1>
          <div className="cert-refline">
            <span><span className="cert-reflabel">Reference</span> <span className="mono">{cert.certificate_id}</span></span>
            <span><span className="cert-reflabel">Issued</span> {fmt(cert.issued_at)}</span>
          </div>
        </div>

        <div className="cert-rows">
          <Row label="rail">{cert.rail.name} ({cert.rail.operator})</Row>
          <Row label="incident window">
            {fmt(cert.incident_window.started_at)} to{" "}
            {cert.incident_window.ongoing ? "ongoing at time of issue" : fmt(cert.incident_window.resolved_at)}
          </Row>
          <Row label="severity">{cert.severity}</Row>
          <Row label="witness quorum">
            {/* Clamped in print CSS: the witness list grows with the incident
                and the sheet must stay one page. Full list is in the JSON. */}
            <span className="cert-clamp">
              {q.reporting_count != null
                ? `${(q.unhealthy_witness_ids || []).length} of ${q.reporting_count} reporting witnesses (${(q.unhealthy_witness_ids || []).join(", ")}) independently marked the rail unhealthy`
                : "not recorded"}
            </span>
          </Row>
          <Row label="your claimed time">{fmt(cert.claimed_timestamp)}</Row>
          <Row label="your transaction ref">
            {cert.claimed_transaction_ref.value ? (
              <>
                {/* Requester-supplied and unbounded server-side, so clamped
                    here. The full value stays in the signed document. */}
                <span className="cert-clamp">{cert.claimed_transaction_ref.value}</span>{" "}
                <em className="cert-unverified">self-reported, not verified</em>
              </>
            ) : (
              <em style={{ color: "var(--stone)" }}>none provided</em>
            )}
          </Row>
          <Row label="log evidence">
            {proven} of {evidence.length} incident log entries carry Merkle inclusion proofs to a
            signed, git-anchored checkpoint
          </Row>
          {/* The full 128-char signature and the aggregator key live in the
              appendix. Nobody reading this across a counter needs them, and
              on paper they cost the page-1 space the disclaimer must have.
              The fingerprint stays: it is what the QR pins. */}
          <Row label="fingerprint"><span className="mono">{bundle.fingerprint}</span></Row>
        </div>

        {/* Directly after the facts it qualifies, and before the QR and the
            how-to. This sentence is the single most important thing on the
            document, so it must not be the element that gets pushed onto a
            second sheet a reader may never turn to. */}
        <div className="cert-disclaimer">{cert.disclaimer}</div>

        <div className="cert-qr-block">
          <div className="cert-qr-wrap" data-verify-url={url}>
            <QRCodeSVG value={url} size={104} level="M" marginSize={3} />
          </div>
          <div className="cert-qr-text">
            <strong>Scan to check this certificate against the aggregator's record.</strong>
            <div className="cert-qr-url mono">{url}</div>
            <p>
              Scanning proves nothing by itself: it opens the verifier, which independently
              re-checks the aggregator's signature, the Merkle inclusion proofs and the git-anchored
              checkpoint roots, and reports each of the three separately.
            </p>
          </div>
        </div>

        <footer className="cert-footer">
          <span>
            Issued by the DPI Sentinel aggregator, Ed25519 key{" "}
            <span className="mono">{(bundle.aggregator_public_key_hex || "").slice(0, 24)}…</span>
          </span>
          {/* Deliberately avoids the word "valid". This sheet was fetched, not
              verified, and the one word it must never print is the one a
              reader would take as a verdict. */}
          <span>Issued electronically; no handwritten signature is required · page 1 of 1</span>
        </footer>
      </section>

      {/* Screen-only from here down. Prose the sheet has no room for, kept
          in full for anyone reading on a screen. */}
      <div className="no-print">
        <div className="cert-howto">
          <div className="cert-appendix-sub">How to check this document</div>
          <ol>
            <li>Scan the code above, or type the address printed beneath it.</li>
            <li>Or paste the signed JSON file at <span className="mono">{publicBase()}#/verify</span>.</li>
            <li>
              Or, entirely offline, run <span className="mono">verify_log.py</span> against the
              publicly anchored checkpoint history and confirm the roots cited in the appendix.
            </li>
          </ol>
          <p className="cert-fp-note">
            The fingerprint pins which document this paper refers to: if the printed text was
            altered after issue, it will not match the record this code resolves to. It is not a
            defence against the aggregator itself, which holds the signing key. Independent trust
            rests on the witness signatures and the external checkpoint anchor, not on this page.
          </p>
        </div>

      {/* ---------- Screen only: the machine-checkable evidence ---------- */}
      <section className="cert-appendix">
        <h2 className="cert-sheet-title" style={{ fontSize: 22 }}>Technical appendix</h2>
        <p className="cert-appendix-intro">
          This appendix reproduces the machine-checkable evidence for reading. Verification consumes
          the signed JSON file, not this page.
        </p>

        <div className="cert-appendix-sub">Document signature</div>
        <div className="cert-hash-pair">
          <div><span>aggregator key</span> {bundle.aggregator_public_key_hex}</div>
          <div><span>signature</span> {bundle.signature}</div>
          <div><span>fingerprint</span> {bundle.fingerprint}</div>
        </div>

        <div className="cert-appendix-sub">Witness quorum snapshot</div>
        <pre className="cert-pre">{JSON.stringify(q, null, 2)}</pre>

        <div className="cert-appendix-sub">Hash-chain entries and inclusion proofs</div>
        {evidence.map((e) => (
          <EvidenceEntry key={e.sequence_number} entry={e} />
        ))}
      </section>
      </div>
    </div>
  );
}
