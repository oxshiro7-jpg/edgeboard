// Small SVG charts drawn to one scale per axis. Text uses ink tokens; series
// identity is carried by line style and a direct label, never colour alone.

const W = 640;

function ticks(lo: number, hi: number, n = 5) {
  const span = hi - lo || 1;
  const step = Math.pow(10, Math.floor(Math.log10(span / n)));
  const mult = [1, 2, 5, 10].find((m) => span / (step * m) <= n) ?? 10;
  const s = step * mult;
  const out: number[] = [];
  for (let v = Math.ceil(lo / s) * s; v <= hi + 1e-9; v += s) out.push(Number(v.toFixed(6)));
  return out;
}

export function EquityChart({ points, height = 240, unit = "u" }: {
  points: { n: number; profit: number; expected: number }[]; height?: number; unit?: string;
}) {
  if (points.length < 2) return <p className="text-sm text-muted">Not enough settled picks to draw a curve.</p>;
  const pad = { l: 48, r: 92, t: 12, b: 28 };
  const xs = (n: number) => pad.l + ((n - 1) / (points.length - 1)) * (W - pad.l - pad.r);
  const vals = points.flatMap((p) => [p.profit, p.expected, 0]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const ys = (v: number) => pad.t + (1 - (v - lo) / (hi - lo || 1)) * (height - pad.t - pad.b);
  const path = (k: "profit" | "expected") => points.map((p, i) => `${i ? "L" : "M"}${xs(p.n).toFixed(1)},${ys(p[k]).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${height}`} className="h-auto w-full" role="img"
      aria-label={`Cumulative profit ${last.profit.toFixed(1)} ${unit} against expected ${last.expected.toFixed(1)} ${unit} over ${points.length} picks`}>
      {ticks(lo, hi).map((t) => (
        <g key={t}>
          <line x1={pad.l} x2={W - pad.r} y1={ys(t)} y2={ys(t)} stroke="rgb(var(--line))" strokeWidth={t === 0 ? 1.5 : 1} />
          <text x={pad.l - 6} y={ys(t) + 4} textAnchor="end" fontSize="11" fill="rgb(var(--muted))">{t}</text>
        </g>
      ))}
      <path d={path("expected")} fill="none" stroke="rgb(var(--ink-2))" strokeWidth={2} strokeDasharray="5 4" />
      <path d={path("profit")} fill="none" stroke="rgb(var(--home))" strokeWidth={2} strokeLinejoin="round" />
      <circle cx={xs(last.n)} cy={ys(last.profit)} r={4} fill="rgb(var(--home))" />
      <text x={xs(last.n) + 8} y={ys(last.profit) + 4} fontSize="12" fill="rgb(var(--ink))">Actual {last.profit.toFixed(1)}{unit}</text>
      <text x={xs(last.n) + 8} y={ys(last.expected) + 4} fontSize="12" fill="rgb(var(--ink-2))">Expected {last.expected.toFixed(1)}{unit}</text>
      <text x={pad.l} y={height - 8} fontSize="11" fill="rgb(var(--muted))">Pick 1</text>
      <text x={W - pad.r} y={height - 8} textAnchor="end" fontSize="11" fill="rgb(var(--muted))">Pick {last.n}</text>
    </svg>
  );
}

export function CalibrationChart({ rows, height = 300 }: {
  rows: { bucket: string; n: number; predicted: number | null; actual: number | null; ci_low: number | null; ci_high: number | null; flag: string | null }[];
  height?: number;
}) {
  const pad = { l: 48, r: 16, t: 12, b: 36 };
  const size = Math.min(W, height + 120);
  const xs = (v: number) => pad.l + v * (size - pad.l - pad.r);
  const ys = (v: number) => pad.t + (1 - v) * (height - pad.t - pad.b);
  const pts = rows.filter((r) => r.n > 0 && r.predicted !== null);
  return (
    <svg viewBox={`0 0 ${size} ${height}`} className="h-auto w-full max-w-[560px]" role="img"
      aria-label="Calibration: predicted probability against actual win rate per bucket">
      {[0, 0.2, 0.4, 0.6, 0.8, 1].map((t) => (
        <g key={t}>
          <line x1={xs(0)} x2={xs(1)} y1={ys(t)} y2={ys(t)} stroke="rgb(var(--line))" />
          <text x={pad.l - 6} y={ys(t) + 4} textAnchor="end" fontSize="11" fill="rgb(var(--muted))">{t * 100}%</text>
          <text x={xs(t)} y={height - 18} textAnchor="middle" fontSize="11" fill="rgb(var(--muted))">{t * 100}%</text>
        </g>
      ))}
      <line x1={xs(0)} y1={ys(0)} x2={xs(1)} y2={ys(1)} stroke="rgb(var(--ink-2))" strokeDasharray="4 4" />
      <text x={xs(0.98)} y={ys(0.98) + 16} textAnchor="end" fontSize="11" fill="rgb(var(--ink-2))">Perfect calibration</text>
      {pts.map((r) => (
        <g key={r.bucket}>
          <title>{`${r.bucket}: predicted ${(r.predicted! * 100).toFixed(1)}%, actual ${(r.actual! * 100).toFixed(1)}%, n=${r.n}`}</title>
          <line x1={xs(r.predicted!)} x2={xs(r.predicted!)} y1={ys(r.ci_low!)} y2={ys(r.ci_high!)} stroke="rgb(var(--ink-2))" strokeWidth={2} />
          <circle cx={xs(r.predicted!)} cy={ys(r.actual!)} r={5} fill={r.flag ? "rgb(var(--critical))" : "rgb(var(--home))"} stroke="rgb(var(--panel))" strokeWidth={2} />
        </g>
      ))}
      <text x={(xs(0) + xs(1)) / 2} y={height - 2} textAnchor="middle" fontSize="11" fill="rgb(var(--muted))">Predicted probability</text>
    </svg>
  );
}

export function BarList({ rows, value, format }: {
  rows: { group: string; picks: number }[]; value: (r: any) => number | null; format: (v: number) => string;
}) {
  const vals = rows.map((r) => value(r) ?? 0);
  const max = Math.max(0.0001, ...vals.map(Math.abs));
  return (
    <ul className="flex flex-col gap-1.5 text-sm">
      {rows.map((r, i) => {
        const v = vals[i];
        const w = (Math.abs(v) / max) * 50;
        return (
          <li key={r.group} className="grid grid-cols-[minmax(0,9rem)_1fr_4.5rem] items-center gap-3">
            <span className="truncate text-ink-2" title={r.group}>{r.group} <span className="text-muted">({r.picks})</span></span>
            <span className="relative h-3 rounded-sm bg-line/40">
              <span className="absolute top-0 h-3 w-px bg-ink-2" style={{ left: "50%" }} />
              <span className={`absolute top-0 h-3 rounded-sm ${v >= 0 ? "bg-ink-2" : "bg-muted"}`}
                style={v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
            </span>
            <span className="num text-right text-ink">{format(v)}</span>
          </li>
        );
      })}
    </ul>
  );
}
