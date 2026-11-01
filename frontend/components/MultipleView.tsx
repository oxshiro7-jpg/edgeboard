import type { ReactNode } from "react";
import type { Multiple, OptimiseResult } from "@/lib/lab-types";
import { RiskBadge } from "./ui/badges";
import { fmtPct, fmtSigned } from "./ui/kit";
import type { RiskCategory } from "@/lib/types";

export function MultipleView({ m, title }: { m: Multiple; title?: string }) {
  return (
    <div className="flex flex-col gap-3">
      {title && <h3 className="text-base font-semibold">{title}</h3>}
      <ol className="divide-y divide-line/60 text-sm">
        {m.legs.map((l) => (
          <li key={`${l.fixture_id}${l.key}`} className="flex flex-wrap justify-between gap-2 py-2">
            <span><span className="text-ink">{l.label}</span> <span className="text-muted">{l.match}</span></span>
            <span className="num text-ink-2">
              {l.odds.toFixed(2)}, model {fmtPct(l.model_probability, 0)}, EV {fmtSigned(l.ev)}, confidence {l.confidence.toFixed(0)}
            </span>
          </li>
        ))}
      </ol>
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <KV k="Bookmaker odds" v={m.combined_odds.toFixed(2)} />
        <KV k="Fair model odds" v={m.fair_odds?.toFixed(2) ?? "—"} />
        <KV k="Hit probability" v={fmtPct(m.probability)} sub={Math.abs(m.probability - m.naive_probability) > 0.002 ? `${fmtPct(m.naive_probability)} if legs were independent` : undefined} />
        <KV k="Expected value" v={fmtSigned(m.ev)} />
        <KV k="Confidence" v={m.confidence.toFixed(0)} />
        <KV k="Risk" v={<RiskBadge risk={m.risk as RiskCategory} />} />
        <KV k="Slip health" v={`${m.health}/100`} />
        <KV k="Weakest data quality" v={m.data_quality.toFixed(0)} />
      </dl>
      {m.correlations.length > 0 && (
        <div className="rounded border border-warn/40 p-3 text-sm">
          <p className="font-medium text-ink">Correlated picks</p>
          <ul className="mt-1 list-disc pl-4 text-ink-2">{m.correlations.map((c) => <li key={c.a + c.b}>{c.text}</li>)}</ul>
        </div>
      )}
      {m.notes.length > 0 && <ul className="text-xs text-muted">{m.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
    </div>
  );
}

function KV({ k, v, sub }: { k: string; v: ReactNode; sub?: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{k}</dt>
      <dd className="num text-lg text-ink">{v}</dd>
      {sub && <dd className="text-xs text-muted">{sub}</dd>}
    </div>
  );
}

export function OptimiseView({ r }: { r: OptimiseResult }) {
  const o = r.original, n = r.optimised;
  return (
    <div className="flex flex-col gap-4">
      <table className="w-full max-w-xl text-sm">
        <thead><tr className="border-b border-line"><th className="th pl-0" /><th className="th text-right">Original</th><th className="th text-right">Optimised</th></tr></thead>
        <tbody>
          {([["Legs", o.legs.length, n.legs.length], ["Bookmaker odds", o.combined_odds.toFixed(2), n.combined_odds.toFixed(2)],
            ["Fair model odds", o.fair_odds?.toFixed(2) ?? "—", n.fair_odds?.toFixed(2) ?? "—"],
            ["Estimated hit probability", fmtPct(o.probability), fmtPct(n.probability)],
            ["Expected value", fmtSigned(o.ev), fmtSigned(n.ev)], ["Health", o.health, n.health]] as [string, ReactNode, ReactNode][]).map(([k, a, b]) => (
            <tr key={k} className="border-b border-line/60"><td className="py-1.5 text-ink-2">{k}</td><td className="num text-right">{a}</td><td className="num text-right text-ink">{b}</td></tr>
          ))}
        </tbody>
      </table>
      {r.removed.length ? (
        <div>
          <h4 className="text-sm font-medium">Removed</h4>
          <ul className="mt-1 flex flex-col gap-2 text-sm">
            {r.removed.map((x) => (
              <li key={x.leg.fixture_id + x.leg.key}>
                <span className="text-ink">{x.leg.label}</span> <span className="text-muted">{x.leg.match}</span>
                <div className="text-ink-2">Why: {x.reasons.join("; ")}. {x.effect}</div>
              </li>
            ))}
          </ul>
        </div>
      ) : <p className="text-sm text-ink-2">No leg has a problem worth removing; the multiple is already as clean as it gets.</p>}
    </div>
  );
}
