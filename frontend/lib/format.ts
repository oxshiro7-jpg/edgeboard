import type { RiskCategory } from "./types";

export const pct = (p: number | null | undefined, digits = 0) =>
  p === null || p === undefined ? "—" : `${(p * 100).toFixed(digits)}%`;

export const signedPct = (p: number | null | undefined, digits = 1) =>
  p === null || p === undefined ? "—" : `${p >= 0 ? "+" : "−"}${Math.abs(p * 100).toFixed(digits)}%`;

export const pp = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)} pp`;

export function odds(o: number | null | undefined, format: "decimal" | "fractional" = "decimal") {
  if (o === null || o === undefined) return "—";
  if (format === "decimal") return o.toFixed(2);
  // continued-fraction approximation, denominator <= 20
  const x = o - 1;
  let best = { n: Math.round(x), d: 1 };
  for (let d = 1; d <= 20; d++) {
    const n = Math.round(x * d);
    if (Math.abs(n / d - x) < Math.abs(best.n / best.d - x)) best = { n, d };
  }
  return `${best.n}/${best.d}`;
}

export const kickoff = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export const ago = (iso: string | null) => {
  if (!iso) return "unknown";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return `${Math.round(mins / 1440)} d ago`;
};

// Status colours mark the dot beside the label; the label itself stays in ink.
export const RISK_DOT: Record<RiskCategory, string> = {
  "Very Low": "bg-good",
  Low: "bg-good",
  Moderate: "bg-warn",
  High: "bg-serious",
  "Very High": "bg-critical",
};

export function confidenceTone(c: number | null) {
  if (c === null) return "text-muted";
  if (c >= 80) return "text-ink";
  if (c >= 60) return "text-ink-2";
  return "text-muted";
}
