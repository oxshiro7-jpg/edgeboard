"use client";
import clsx from "clsx";
import { useMemo, useState } from "react";
import type { Market } from "@/lib/types";
import { odds, pct, pp } from "@/lib/format";
import { Confidence, EV, RiskBadge } from "./ui/badges";

const FAMILIES: [string, string][] = [
  ["all", "All"], ["result", "Result"], ["goals", "Goals"], ["btts", "BTTS"], ["handicap", "Handicaps"],
  ["first_half", "First half"], ["second_half", "Second half"], ["cards", "Cards"], ["corners", "Corners"],
];

export function MarketTable({ markets }: { markets: Market[] }) {
  const [fam, setFam] = useState("all");
  const [show, setShow] = useState<"priced" | "recommended" | "all">("priced");
  const [open, setOpen] = useState<string | null>(null);

  const present = new Set(markets.map((m) => m.family));
  const rows = useMemo(() => markets
    .filter((m) => fam === "all" || m.family === fam)
    .filter((m) => show === "all" || (show === "recommended" ? m.status === "recommended" : m.bookmaker_odds !== null))
    .sort((a, b) => (b.pick_score ?? -1) - (a.pick_score ?? -1)), [markets, fam, show]);

  return (
    <section className="panel overflow-hidden" aria-labelledby="mk-h">
      <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
        <h2 id="mk-h" className="mr-3 text-base font-semibold">Markets</h2>
        <div role="tablist" aria-label="Market family" className="flex flex-wrap gap-1">
          {FAMILIES.filter(([k]) => k === "all" || present.has(k)).map(([k, l]) => (
            <button key={k} role="tab" aria-selected={fam === k} onClick={() => setFam(k)}
              className={clsx("rounded px-2 py-1 text-sm", fam === k ? "bg-raised text-ink" : "text-ink-2 hover:text-ink")}>{l}</button>
          ))}
        </div>
        <label className="ml-auto flex items-center gap-2 text-sm text-ink-2">
          Show
          <select value={show} onChange={(e) => setShow(e.target.value as typeof show)}
            className="rounded border border-line bg-raised px-2 py-1 text-sm text-ink">
            <option value="priced">Priced markets</option>
            <option value="recommended">Recommended only</option>
            <option value="all">Everything the model prices</option>
          </select>
        </label>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-line">
            <tr>
              <th className="th">Selection</th>
              <th className="th text-right">Model</th>
              <th className="th text-right">Market (no-vig)</th>
              <th className="th text-right">Edge</th>
              <th className="th text-right">Fair</th>
              <th className="th text-right">Odds</th>
              <th className="th text-right">EV</th>
              <th className="th">Confidence</th>
              <th className="th">Risk</th>
              <th className="th text-right">Score</th>
              <th className="th">Decision</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <FragmentRow key={m.key} m={m} open={open === m.key} toggle={() => setOpen(open === m.key ? null : m.key)} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FragmentRow({ m, open, toggle }: { m: Market; open: boolean; toggle: () => void }) {
  const decision = m.status === "recommended" ? m.label_tag : m.status === "no_price" ? "No price" : "Pass";
  return (
    <>
      <tr className="cursor-pointer border-b border-line/60 hover:bg-raised/50" onClick={toggle}>
        <td className="td">
          <button className="text-left text-ink" aria-expanded={open}>{m.label}</button>
        </td>
        <td className="td num text-right">{pct(m.model_probability, 1)}</td>
        <td className="td num text-right text-ink-2">{pct(m.no_vig_probability, 1)}</td>
        <td className="td num text-right">{pp(m.edge_pp)}</td>
        <td className="td num text-right text-ink-2">{odds(m.fair_odds)}</td>
        <td className="td num text-right">{odds(m.bookmaker_odds)}</td>
        <td className="td text-right"><EV ev={m.ev} /></td>
        <td className="td"><Confidence value={m.confidence} compact /></td>
        <td className="td"><RiskBadge risk={m.risk} /></td>
        <td className="td num text-right">{m.pick_score ?? "—"}</td>
        <td className={clsx("td", m.status === "recommended" ? "text-ink" : "text-muted")}>{decision}</td>
      </tr>
      {open && (
        <tr className="border-b border-line/60 bg-bg/40">
          <td colSpan={11} className="px-3 py-3">
            <div className="grid gap-4 text-sm md:grid-cols-3">
              <div>
                <h4 className="font-medium text-ink">{m.status === "recommended" ? "Why" : "Why not"}</h4>
                <ul className="mt-1 list-disc space-y-1 pl-4 text-ink-2">
                  {(m.status === "recommended" ? m.why ?? [] : m.reasons_rejected).map((r) => <li key={r}>{r}</li>)}
                </ul>
              </div>
              <Factors title={`Confidence ${m.confidence}/100 (${m.confidence_level})`} f={m.confidence_factors} fmt={(v) => `×${v.toFixed(2)}`} />
              <Factors title={`Risk ${m.risk_score} (${m.risk})`} f={m.risk_factors} fmt={(v) => `+${v.toFixed(1)}`} />
            </div>
            {(m.settlement.push > 0 || m.settlement.half_win > 0 || m.settlement.half_loss > 0) && (
              <p className="mt-2 text-xs text-muted">
                Settlement: win {pct(m.settlement.win)}, half-win {pct(m.settlement.half_win)}, push {pct(m.settlement.push)},
                half-loss {pct(m.settlement.half_loss)}, loss {pct(m.settlement.loss)}. Probability shown is the
                equivalent binary probability at fair odds.
              </p>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function Factors({ title, f, fmt }: { title: string; f: Record<string, number>; fmt: (v: number) => string }) {
  return (
    <div>
      <h4 className="font-medium text-ink">{title}</h4>
      <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-ink-2">
        {Object.entries(f).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="capitalize">{k.replaceAll("_", " ")}</dt>
            <dd className="num text-right">{fmt(v)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
