import React, { useEffect, useState } from "react";
import { api } from "./api";

// The trust machinery, made visible on the status page itself.
//
// Everything here was already exposed by the API and already load-bearing in
// the backend, but none of it reached the page, so the strongest claim this
// project makes ("no single server's word is trusted, and the record cannot
// be quietly rewritten") was invisible to anyone who did not read the code.
//
// HONESTY RULE, same one quorum.py and the certificate verifier follow: this
// panel reports what is RECORDED, never a verdict it has not earned. It shows
// the chain's length and its latest git anchor; it does not print "verified",
// because verifying means walking every entry and recomputing every hash,
// which is verify_log.py's job, not a page render. Overstating here would be
// the exact failure this project is built to argue against.

function shortHex(hex, n = 10) {
  return hex ? `${hex.slice(0, n)}…` : "n/a";
}

function timeAgo(iso) {
  if (!iso) return "never";
  const seconds = Math.max(0, (Date.now() - new Date(iso + "Z").getTime()) / 1000);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

function WitnessRow({ witness }) {
  return (
    <div className="witness-row">
      <div className="witness-id">
        <span className={`witness-dot${witness.reporting ? "" : " witness-dot--quiet"}`} />
        {witness.slug}
      </div>
      <div className="witness-key" title={witness.public_key_hex}>
        {shortHex(witness.public_key_hex, 16)}
      </div>
      <div className="witness-rails">
        {witness.assigned_rails.length ? witness.assigned_rails.join(", ") : "no rails assigned"}
      </div>
      <div className="witness-seen">
        {witness.reporting ? `reporting, ${timeAgo(witness.last_seen_at)}` : `quiet since ${timeAgo(witness.last_seen_at)}`}
      </div>
    </div>
  );
}

export default function VerificationLedger() {
  const [witnesses, setWitnesses] = useState([]);
  const [summary, setSummary] = useState(null);
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState(null);
  const [showEntries, setShowEntries] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const [w, s, e] = await Promise.all([
          api.getWitnesses(),
          api.getLogSummary(),
          api.getLog(6),
        ]);
        setWitnesses(w);
        setSummary(s);
        setEntries(e);
        setError(null);
      } catch (err) {
        setError(err.message);
      }
    };
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, []);

  if (error) {
    return (
      <section className="ledger-panel">
        <h2 className="section-title">How this page knows</h2>
        <div className="ledger-error">Could not load verification data ({error}).</div>
      </section>
    );
  }

  const reporting = witnesses.filter((w) => w.reporting).length;
  const cp = summary?.latest_checkpoint;

  return (
    <section className="ledger-panel">
      <div className="ledger-head-row">
        <h2 className="section-title">How this page knows</h2>
        <span className="ledger-note">
          {reporting} of {witnesses.length} witnesses reporting
        </span>
      </div>
      <p className="ledger-intro">
        No single server decides whether a rail is up. Independent witness services each run their own
        probes and sign what they saw with their own key, and every accepted observation is appended to
        a hash-chained log that is periodically sealed into a git repository kept separate from the
        application database. In production that repository is pushed to an external remote, which is
        what puts a copy of the record beyond the operator's reach.
      </p>

      <h3 className="ledger-sub">Witnesses the aggregator will accept</h3>
      <div className="witness-table">
        <div className="witness-row witness-row--head">
          <div>witness</div>
          <div>public key</div>
          <div>covers</div>
          <div>status</div>
        </div>
        {witnesses.map((w) => (
          <WitnessRow key={w.slug} witness={w} />
        ))}
      </div>
      <p className="ledger-foot">
        Each key is fetched from the witness itself when the aggregator starts, and is the only key an
        observation from that witness is ever checked against. A witness that goes quiet stays listed
        and stays assigned: silence lowers confidence, it never counts as an all-clear.
      </p>

      <h3 className="ledger-sub">The record</h3>
      <div className="chain-stats">
        <div className="chain-stat">
          <span className="chain-stat-value">{summary ? summary.entry_count.toLocaleString("en-IN") : "…"}</span>
          <span className="chain-stat-label">entries in the chain</span>
        </div>
        <div className="chain-stat">
          <span className="chain-stat-value">{summary ? summary.checkpoint_count.toLocaleString("en-IN") : "…"}</span>
          <span className="chain-stat-label">sealed checkpoints</span>
        </div>
        <div className="chain-stat">
          <span className="chain-stat-value">{cp ? shortHex(cp.merkle_root, 8) : "n/a"}</span>
          <span className="chain-stat-label">latest merkle root</span>
        </div>
        <div className="chain-stat">
          <span className={`chain-stat-value${cp?.git_committed ? " chain-stat-value--anchored" : ""}`}>
            {cp?.git_committed ? shortHex(cp.git_commit_sha, 8) : "not anchored"}
          </span>
          <span className="chain-stat-label">external git anchor</span>
        </div>
      </div>
      <p className="ledger-foot">
        Every entry commits to the one before it, so altering any past record changes every hash after
        it. Checkpoints seal batches of entries under a signed Merkle root that is committed to a
        separate git repository, which is what makes silent edits by the operator detectable rather
        than merely discouraged. These are the figures on record; confirming them means recomputing the
        whole chain, which is what{" "}
        <span className="mono-inline">verify_log.py</span> and the{" "}
        <a href="#/verify">certificate verifier</a> do.
      </p>

      <button type="button" className="btn-ghost" onClick={() => setShowEntries((v) => !v)}>
        {showEntries ? "Hide latest entries" : "Show latest entries"}
      </button>

      {showEntries && (
        <div className="chain-entries">
          {entries.map((e) => (
            <div key={e.id} className="chain-entry">
              <span className="chain-entry-seq">#{e.sequence_number}</span>
              <span className="chain-entry-type">{e.entry_type}</span>
              <span className="chain-entry-hash" title={`entry hash: ${e.entry_hash}\nprev hash: ${e.prev_hash}`}>
                {shortHex(e.prev_hash, 8)} → {shortHex(e.entry_hash, 8)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
