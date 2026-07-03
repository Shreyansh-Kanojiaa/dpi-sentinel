import React from "react";

/**
 * Renders the last N probe points as a pulse strip: a thin horizontal
 * line that traces the simulated success rate, with injected/incident
 * points marked. Designed to read like a seismograph or ECG strip rather
 * than a dashboard chart — this is the signature visual element.
 */
export default function PulseStrip({ points = [], color = "#1A7A5E", height = 40, width = 220 }) {
  if (!points.length) {
    return (
      <div
        style={{
          width,
          height,
          display: "flex",
          alignItems: "center",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--stone)",
        }}
      >
        awaiting first probe…
      </div>
    );
  }

  const padded = points.length < 2 ? [points[0], points[0]] : points;
  const n = padded.length;
  const stepX = width / Math.max(n - 1, 1);

  const toY = (rate) => {
    const r = rate ?? 0;
    const clamped = Math.max(0, Math.min(1, r));
    // Compress the y-range to 0.6..1.0 mapped across the full height, so
    // everyday jitter (98-100%) actually produces visible texture instead
    // of a flat line pinned at the top. Anything below 0.6 clips to the floor,
    // which is fine — by then it's a visually obvious incident anyway.
    const floor = 0.6;
    const normalized = Math.max(0, (clamped - floor) / (1 - floor));
    return height - normalized * height;
  };

  const pathD = padded
    .map((p, i) => `${i === 0 ? "M" : "L"} ${(i * stepX).toFixed(1)} ${toY(p.rate).toFixed(1)}`)
    .join(" ");

  const hasIncident = padded.some((p) => p.injected);

  return (
    <svg width={width} height={height} role="img" aria-label="recent probe history">
      {/* baseline reference at 98.5% threshold */}
      <line
        x1={0}
        x2={width}
        y1={toY(0.985)}
        y2={toY(0.985)}
        stroke="var(--stone-line)"
        strokeWidth={1}
        strokeDasharray="2,3"
      />
      <path
        d={pathD}
        fill="none"
        stroke={hasIncident ? "var(--rust)" : color}
        strokeWidth={1.6}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {padded.map((p, i) =>
        p.injected ? (
          <circle key={i} cx={i * stepX} cy={toY(p.rate)} r={2.2} fill="var(--rust)" />
        ) : null
      )}
    </svg>
  );
}
