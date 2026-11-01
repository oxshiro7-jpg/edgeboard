import clsx from "clsx";
import type { LabelTag, RiskCategory } from "@/lib/types";
import { RISK_DOT } from "@/lib/format";

export function RiskBadge({ risk }: { risk: RiskCategory | null }) {
  if (!risk) return <span className="text-muted">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5 text-ink-2 text-sm">
      <span aria-hidden className={clsx("h-2 w-2 rounded-full", RISK_DOT[risk])} />
      {risk}
    </span>
  );
}

/** Confidence as a short 5-segment gauge plus the number; never colour alone. */
export function Confidence({ value, compact = false }: { value: number | null; compact?: boolean }) {
  if (value === null) return <span className="text-muted">—</span>;
  const filled = Math.round(value / 20);
  return (
    <span className="inline-flex items-center gap-2" title={`Confidence ${value}/100`}>
      <span className="num text-ink">{value.toFixed(0)}</span>
      {!compact && (
        <span aria-hidden className="flex gap-[2px]">
          {Array.from({ length: 5 }, (_, i) => (
            <span key={i} className={clsx("h-2.5 w-1.5 rounded-[1px]", i < filled ? "bg-ink-2" : "bg-line")} />
          ))}
        </span>
      )}
    </span>
  );
}

const TAG_STYLE: Partial<Record<LabelTag, string>> = {
  "Highest Confidence": "border-ink text-ink",
  "Strong Pick": "border-home/70 text-ink",
  "Conservative Pick": "border-line text-ink-2",
  "Value Pick": "border-good/70 text-ink",
  "Higher Risk": "border-serious/70 text-ink",
  Avoid: "border-critical/60 text-ink-2",
  "Insufficient Edge": "border-line text-muted",
};

export function TagChip({ tag }: { tag: LabelTag }) {
  return <span className={clsx("chip", TAG_STYLE[tag])}>{tag}</span>;
}

export function EV({ ev }: { ev: number | null }) {
  if (ev === null) return <span className="text-muted">—</span>;
  return (
    <span className={clsx("num", ev >= 0 ? "text-ink" : "text-muted")}>
      {ev >= 0 ? "+" : "−"}
      {Math.abs(ev * 100).toFixed(1)}%
    </span>
  );
}

const VERIFY_DOT: Record<string, string> = {
  VERIFIED: "bg-good", "ODDS UNAVAILABLE": "bg-warn", "DATA INCOMPLETE": "bg-warn",
  UNVERIFIED: "bg-critical", POSTPONED: "bg-muted", CANCELLED: "bg-muted",
};

/** Verification state, always shown as text with a dot (never colour alone). */
export function VerificationBadge({ status, title }: { status: string; title?: string }) {
  return (
    <span title={title} className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm border border-line px-1.5 py-0.5 text-xs font-semibold text-ink">
      <span aria-hidden className={clsx("h-2 w-2 rounded-full", VERIFY_DOT[status] ?? "bg-line")} />
      {status}
    </span>
  );
}

export function StatusLabel({ status }: { status: string }) {
  const dot: Record<string, string> = {
    "Strong opportunity": "bg-home",
    "Moderate opportunity": "bg-ink-2",
    "No edge": "bg-line",
    Avoid: "bg-critical",
  };
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-ink-2">
      <span aria-hidden className={clsx("h-1.5 w-3 rounded-sm", dot[status] ?? "bg-line")} />
      {status}
    </span>
  );
}
