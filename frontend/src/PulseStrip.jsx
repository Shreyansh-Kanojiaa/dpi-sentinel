import React from "react";

/**
 * Renders the last N probe points as a pulse strip: a thin horizontal
 * line that traces reachability, drawn over a faint strip-chart
 * graticule. Designed to read like a seismograph or ECG strip rather
 * than a dashboard chart — this is the signature visual element.
 *
 * It plots `reachable`, which every point carries because a witness
 * measured it. It deliberately does NOT plot `rate` (the simulated
 * success rate): witnesses don't report one, so that field is null on
 * every observation, and drawing it produced a line pinned to the floor
 * that read as a 0% signal. A strip chart of a number nobody measures is
 * exactly the unearned precision this project argues against.
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

  // Binary trace, inset from both edges so a fully-reachable run still reads
  // as a drawn line rather than clipping against the top of the box.
  const pad = 4;
  const toY = (p) => (p.reachable ? pad : height - pad);

  const pathD = padded
    .map((p, i) => `${i === 0 ? "M" : "L"} ${(i * stepX).toFixed(1)} ${toY(p).toFixed(1)}`)
    .join(" ");

  const hasOutage = padded.some((p) => !p.reachable);
  const strokeColor = hasOutage ? "var(--rust)" : color;
  const last = padded[n - 1];

  // Vertical graticule ticks every ~28px, like the timing marks on a
  // strip-chart recorder's paper feed.
  const ticks = [];
  for (let x = 28; x < width; x += 28) ticks.push(x);

  return (
    <svg width={width} height={height} role="img" aria-label="recent probe reachability">
      {ticks.map((x) => (
        <line key={x} x1={x} x2={x} y1={0} y2={height} stroke="var(--stone-line)" strokeWidth={0.5} opacity={0.6} />
      ))}
      {/* baseline reference at the "unreachable" floor */}
      <line
        x1={0}
        x2={width}
        y1={height - pad}
        y2={height - pad}
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
        !p.reachable ? (
          <circle key={i} cx={i * stepX} cy={toY(p)} r={2.2} fill="var(--rust)" />
        ) : null
      )}
      {/* pen head — where the recorder is writing right now */}
      <circle cx={width - 2.5} cy={toY(last)} r={2.4} fill={strokeColor} />
    </svg>
  );
}
