import React from "react";

/**
 * Renders the last N probe points as a pulse strip: a thin horizontal
 * line that traces the simulated success rate, drawn over a faint
 * strip-chart graticule. Designed to read like a seismograph or ECG
 * strip rather than a dashboard chart — this is the signature visual
 * element.
 */
export default function PulseStrip({ points = [], color = "#17694E", height = 40, width = 220 }) {
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
  const strokeColor = hasIncident ? "var(--rust)" : color;
  const last = padded[n - 1];

  // Vertical graticule ticks every ~28px, like the timing marks on a
  // strip-chart recorder's paper feed.
  const ticks = [];
  for (let x = 28; x < width; x += 28) ticks.push(x);

  return (
    <svg width={width} height={height} role="img" aria-label="recent probe history">
      {ticks.map((x) => (
        <line key={x} x1={x} x2={x} y1={0} y2={height} stroke="var(--stone-line)" strokeWidth={0.5} opacity={0.6} />
      ))}
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
        stroke={strokeColor}
        strokeWidth={1.6}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {padded.map((p, i) =>
        p.injected ? (
          <circle key={i} cx={i * stepX} cy={toY(p.rate)} r={2.2} fill="var(--rust)" />
        ) : null
      )}
      {/* pen head — where the recorder is writing right now */}
      <circle cx={width - 2.5} cy={toY(last.rate)} r={2.4} fill={strokeColor} />
    </svg>
  );
}
