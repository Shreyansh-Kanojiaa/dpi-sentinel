import React, { useEffect, useState } from "react";
import { api } from "./api";
import QrScanner from "./QrScanner";

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
    explains: "Do the cited checkpoint roots match the copies committed to the external git repository, not just the aggregator's own database?",
  },
};

// Pull a certificate reference out of arbitrary text: a pasted id, a URL
// copied off paper, or a string decoded from a scanned QR code.
//
// SECURITY: the scanned case is attacker-controlled — anyone can print a QR
// code. This extracts the id and the fingerprint and discards EVERYTHING
// else, origin included, so nothing downstream ever learns what host the
// text named. Never navigate to the text, and never use its origin as a
// fetch base: a forged certificate could then point a bank clerk's browser
// at a look-alike page that simply prints VALID.
function parseCertificateRef(text) {
  const id = text.match(/\b[0-9a-f]{32}\b/i);
  if (!id) return null;
  // Whitespace is allowed inside the fingerprint: a printed URL wraps across
  // lines and copying it back reintroduces the break. claimedFp strips it.
  const fp = text.match(/[?&]f=([0-9a-f][0-9a-f\s]{0,80})/i);
  return { id: id[0].toLowerCase(), fingerprint: fp ? fp[1] : null };
}

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

export default function VerifyPage({ certificateId = null, fingerprint = null }) {
  const [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [fetched, setFetched] = useState(null);

  // The fingerprint claimed by whatever named the certificate currently on
  // screen: the route, a pasted URL, or a scanned QR code. It is set by the
  // SAME call that loads the document, so it can never belong to a previous
  // one (see loadAndVerify).
  const [claimedFingerprint, setClaimedFingerprint] = useState(null);
  const [scanning, setScanning] = useState(false);

  const onFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    // A PDF read as text is binary noise, which would surface as a confusing
    // "doesn't parse as JSON". Say what is actually true instead: the printed
    // copy is not the verifiable artifact, and here is what to do with it.
    if (f.type === "application/pdf" || /\.pdf$/i.test(f.name)) {
      setRaw("");
      setError(
        "That's the printed copy, which is a human-readable rendering rather than the signed document itself. " +
        "Scan its QR code, or copy the certificate id (or the verify address) printed on it into the box above."
      );
      return;
    }
    setError(null);
    setRaw(await f.text());
  };

  const runVerify = async (bundle) => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.verifyCertificate(bundle));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  // Fetch the document an id names, then run exactly the checks the paste path
  // runs. Shared by the scanned-QR route and the pasted-id/URL path so both
  // reach verification through identical code.
  const loadAndVerify = async (id, fp = null) => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const bundle = await api.getCertificate(id);
      setFetched(bundle);
      setClaimedFingerprint(fp);
      // Show the document that was actually verified, so it can be read,
      // copied and re-checked rather than taken on trust.
      setRaw(JSON.stringify(bundle, null, 2));
      setResult(await api.verifyCertificate(bundle));
    } catch (err) {
      // A 404 is NOT a verdict. Name both possible causes and assert neither;
      // reading it as "invalid" would be exactly the overclaim this project
      // exists to argue against.
      setError(
        err.status === 404
          ? "This aggregator has no certificate on record with that id. That can mean the id was " +
            "misread, or that the paper is not from this service. The id printed beneath the code " +
            "is 32 characters of 0-9 and a-f."
          : err.message
      );
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    const text = raw.trim();
    let bundle;
    try {
      bundle = JSON.parse(text);
    } catch {
      // Not JSON. The most likely reason is someone holding only the printed
      // certificate, copying the id or the verify address off the page. Both
      // resolve to the same document the QR code points at.
      const ref = parseCertificateRef(text);
      if (ref) {
        loadAndVerify(ref.id, ref.fingerprint);
        return;
      }
      setError(
        "That is neither a certificate file nor a certificate id. Paste the signed JSON exactly as " +
        "downloaded, or the 32-character certificate id printed on the paper copy."
      );
      return;
    }
    if (!bundle.certificate || !bundle.signature) {
      setError('Expected a bundle with "certificate" and "signature" fields. Use the file exactly as issued.');
      return;
    }
    runVerify(bundle);
  };

  // A scanned QR is attacker-controllable paper. Take the id and the
  // fingerprint out of it and throw the rest away. NEVER navigate to this
  // string and NEVER use its origin as a fetch base: a forged certificate
  // could then point a clerk's browser at a page that just prints VALID.
  // Returning false means "read, but not ours" and keeps the scanner looking.
  const onScanText = (text) => {
    const ref = parseCertificateRef(text);
    if (!ref) return false;
    loadAndVerify(ref.id, ref.fingerprint);
    return true;
  };

  // Arrived from a scanned QR code (#/verify/<id>?f=<fp>).
  useEffect(() => {
    // The route is just another caller: it passes ITS fingerprint into the
    // load, so the comparison can never outlive the document it belongs to.
    if (certificateId) loadAndVerify(certificateId, fingerprint);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [certificateId]);

  // String comparison ONLY. The browser never hashes and never re-serializes:
  // reproducing canonical_json_bytes in JS would be a second serializer that
  // must stay byte-identical with the Python one forever, which the project
  // forbids. The server derived this fingerprint from the exact signed bytes.
  //
  // Normalise before comparing. A fingerprint is hex, so anything that is not
  // a hex digit got there in transit: the printed URL wraps across lines on
  // paper, and copying it back reintroduces that break as a space or a "+",
  // which URLSearchParams then decodes into the value. Comparing raw produced
  // a false "mismatch" on a genuine certificate, which is worse than useless
  // on a document whose whole job is to be trusted. Stripping non-hex cannot
  // turn a wrong fingerprint into a right one, so this loses no safety.
  // Read ONLY from the per-load state, never from the route prop directly.
  // The route prop is passed into loadAndVerify instead. Preferring the prop
  // here would let a stale route fingerprint be compared against a document
  // loaded later by a scan or a paste, producing a "mismatch" warning on two
  // perfectly genuine certificates.
  const claimedFp = (claimedFingerprint || "").replace(/[^0-9a-f]/gi, "").toLowerCase() || null;
  const fpMatch =
    claimedFp == null || fetched == null
      ? null
      : (fetched.fingerprint || "").toLowerCase().startsWith(claimedFp);

  return (
    <div className="page page--narrow">
      <header style={{ marginBottom: 28 }}>
        <div className="eyebrow" style={{ marginBottom: 14 }}>DPI Sentinel</div>
        <a className="btn-ghost" href="#/">← Back to status page</a>
        <h1 className="masthead-title" style={{ fontSize: 32, marginTop: 18 }}>
          Verify an Evidence Certificate
        </h1>
        <div className="masthead-sub" style={{ maxWidth: 620 }}>
          {certificateId ? (
            <>
              Loaded certificate <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{certificateId}</span>{" "}
              from the aggregator and re-ran the checks. The document is shown below exactly as it
              was fetched, so you can copy it and check it yourself.
            </>
          ) : (
            <>
              Paste or upload a certificate file, exactly as it was downloaded. It doesn't matter
              where the file came from or who forwarded it. Validity is re-derived from the
              cryptography, not from trusting the sender.
            </>
          )}
        </div>
      </header>

      {/* Provenance, reported ABOVE and SEPARATELY from the three
          cryptographic checks. A mismatch and a valid signature are
          compatible and mean something specific: this id resolves to a
          genuinely signed but DIFFERENT document than the paper claims.
          Collapsing that into the verdict would destroy the distinction. */}
      {fpMatch === true && (
        <div className="fp-note fp-note--match">
          Fingerprint matches the printed copy (first {claimedFp.length} hex digits of the digest
          this certificate's signature covers).
        </div>
      )}
      {fpMatch === false && (
        <div className="fp-note fp-note--mismatch">
          <strong>Fingerprint mismatch.</strong> The printed copy cites{" "}
          <span className="mono">{claimedFp}</span>, but the document stored under this
          certificate id begins <span className="mono">{(fetched.fingerprint || "").slice(0, claimedFp.length)}</span>.
          The checks below still apply to the document shown here; they say nothing about the paper
          you are holding. Compare the printed details against the document before relying on either.
        </div>
      )}

      <textarea
        className="verify-input"
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        placeholder={'Paste the signed certificate JSON here, or the 32-character certificate id printed on a paper copy.'}
        spellCheck={false}
      />
      <div className="verify-actions">
        <button className="btn-primary" onClick={verify} disabled={busy || !raw.trim()}>
          {busy ? "Verifying…" : "Verify certificate"}
        </button>
        <button
          type="button"
          className="btn-ghost"
          aria-expanded={scanning}
          onClick={() => {
            // Clear any previous verdict when opening the scanner. Otherwise a
            // VALID banner from an earlier document stays on screen while a
            // new code is being scanned, and a junk QR appears to have
            // verified. A stale verdict next to a fresh scan is exactly the
            // kind of ambiguity this page must not produce.
            if (!scanning) {
              setResult(null);
              setFetched(null);
              setError(null);
            }
            setScanning((v) => !v);
          }}
        >
          {scanning ? "Close scanner" : "Scan QR code"}
        </button>
        <label className="verify-upload">
          {/* PDFs are accepted by the picker on purpose: someone holding only
              the printed copy will try it, and onFile then explains what to
              do instead of the picker silently greying the file out. */}
          …or upload the file:{" "}
          <input type="file" accept=".json,application/json,.pdf,application/pdf" onChange={onFile} />
        </label>
      </div>

      {/* Mounts only on an explicit click: that satisfies iOS Safari's
          user-gesture requirement for play(), and means the camera is never
          live unless somebody asked for it. */}
      {scanning && <QrScanner onText={onScanText} onClose={() => setScanning(false)} />}

      {error && <div className="copilot-error">{error}</div>}

      {result && (
        <section style={{ marginTop: 28 }}>
          <div className={`verdict-banner ${result.valid ? "verdict-banner--valid" : "verdict-banner--invalid"}`}>
            {result.valid
              ? "VALID: every evaluable check passed"
              : `INVALID: failed ${result.failed_checks.join(", ")}`}
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
            quorum during its window, never the outcome of any individual transaction.
          </div>
        </section>
      )}
    </div>
  );
}
