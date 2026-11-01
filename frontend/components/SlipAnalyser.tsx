"use client";
import { useState } from "react";
import { call } from "@/lib/client";
import type { Analysis, MatchEntry } from "@/lib/types";
import type { SlipResult } from "@/lib/lab-types";
import { useSlip } from "./SlipContext";
import { MultipleView, OptimiseView } from "./MultipleView";
import { RiskBadge } from "./ui/badges";
import { Button, ErrorNote, Field, Panel, fmtPct, fmtSigned, inputCls } from "./ui/kit";
import type { RiskCategory } from "@/lib/types";

type Row = { fixture_id: string; market_key: string; label: string; odds: string };

export function SlipAnalyser({ matches }: { matches: { id: string; label: string }[] }) {
  const slip = useSlip();
  const [rows, setRows] = useState<Row[]>(() => slip.legs.map((l) => ({ fixture_id: l.fixture_id, market_key: l.market_key, label: `${l.label} (${l.match})`, odds: l.odds?.toFixed(2) ?? "" })));
  const [fixture, setFixture] = useState(matches[0]?.id ?? "");
  const [markets, setMarkets] = useState<Analysis["markets"]>([]);
  const [market, setMarket] = useState("");
  const [odds, setOdds] = useState("");
  const [res, setRes] = useState<SlipResult | null>(null);
  const [showFix, setShowFix] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function loadMarkets(id: string) {
    setFixture(id); setMarket("");
    try {
      const e = await call<MatchEntry>(`matches/${encodeURIComponent(id)}`);
      if (!e.analysis) { setMarkets([]); setErr(`${e.status}: ${e.messages.join("; ")}`); return; }
      setMarkets(e.analysis.markets);
    } catch (e) { setErr((e as Error).message); }
  }

  function addRow() {
    const m = markets.find((x) => x.key === market);
    if (!m) { setErr("Choose a market first"); return; }
    if (odds && !(Number(odds) > 1)) { setErr("Odds must be decimal and above 1.00"); return; }
    const match = matches.find((x) => x.id === fixture)?.label ?? "";
    setRows([...rows, { fixture_id: fixture, market_key: market, label: `${m.label} (${match})`, odds }]);
    setErr(null); setOdds("");
  }

  async function analyse() {
    setErr(null); setShowFix(false);
    try {
      setRes(await call<SlipResult>("slip/analyse", { legs: rows.map((r) => ({ fixture_id: r.fixture_id, market_key: r.market_key, odds: r.odds ? Number(r.odds) : undefined })) }));
    } catch (e) { setErr((e as Error).message); }
  }

  return (
    <div className="flex flex-col gap-6">
      <Panel title="Selections">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Match" htmlFor="m-fix">
            <select id="m-fix" value={fixture} onChange={(e) => loadMarkets(e.target.value)} onFocus={() => !markets.length && loadMarkets(fixture)} className={inputCls}>
              {matches.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </Field>
          <Field label="Market" htmlFor="m-mk">
            <select id="m-mk" value={market} onChange={(e) => setMarket(e.target.value)} onFocus={() => !markets.length && loadMarkets(fixture)} className={inputCls}>
              <option value="">Choose a market</option>
              {markets.map((m) => <option key={m.key} value={m.key}>{m.label}{m.bookmaker_odds ? ` (${m.bookmaker_odds.toFixed(2)})` : ""}</option>)}
            </select>
          </Field>
          <Field label="Your odds (optional)" htmlFor="m-odds">
            <input id="m-odds" value={odds} onChange={(e) => setOdds(e.target.value)} inputMode="decimal" placeholder="e.g. 1.85" className={`${inputCls} w-28`} />
          </Field>
          <Button onClick={addRow}>Add selection</Button>
        </div>
        <ul className="mt-4 divide-y divide-line/60 text-sm">
          {rows.map((r, i) => (
            <li key={i} className="flex items-center justify-between gap-3 py-2">
              <span className="text-ink">{r.label}</span>
              <span className="flex items-center gap-3"><span className="num text-ink-2">{r.odds || "market price"}</span>
                <Button onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</Button></span>
            </li>
          ))}
          {!rows.length && <li className="py-2 text-ink-2">No selections yet. Add them here or from Best picks.</li>}
        </ul>
        <div className="mt-3 flex gap-2">
          <Button primary disabled={!rows.length} onClick={analyse}>Analyse slip</Button>
        </div>
        <div className="mt-2"><ErrorNote message={err} /></div>
      </Panel>

      {res && (
        <Panel title={`Bet slip health ${res.health}/100`}>
          <p className="text-ink">Main problem: {res.main_problem}</p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-line"><tr>
                <th className="th pl-0">Selection</th><th className="th text-right">Probability</th><th className="th text-right">Fair</th>
                <th className="th text-right">Odds</th><th className="th text-right">EV</th><th className="th">Confidence</th><th className="th">Risk</th><th className="th">Issues</th>
              </tr></thead>
              <tbody>
                {res.legs.map((l) => (
                  <tr key={l.fixture_id + l.key} className="border-b border-line/60">
                    <td className="td pl-0"><span className="text-ink">{l.label}</span><div className="text-xs text-muted">{l.match}</div></td>
                    <td className="td num text-right">{fmtPct(l.model_probability)}</td>
                    <td className="td num text-right text-ink-2">{l.fair_odds?.toFixed(2) ?? "—"}</td>
                    <td className="td num text-right">{l.odds.toFixed(2)}</td>
                    <td className="td num text-right">{fmtSigned(l.ev)}</td>
                    <td className="td num">{l.confidence.toFixed(0)}</td>
                    <td className="td"><RiskBadge risk={l.risk as RiskCategory} /></td>
                    <td className="td whitespace-normal text-ink-2">{l.problems.join("; ") || "None"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-4"><MultipleView m={res.slip} /></div>
          {res.fix && <Button primary className="mt-4" onClick={() => setShowFix(true)}>Fix my slip</Button>}
          {showFix && res.fix && (
            <div className="mt-4 border-t border-line pt-4">
              <OptimiseView r={res.fix} />
              {res.replacements.length > 0 && (
                <div className="mt-3 text-sm">
                  <h4 className="font-medium">Possible replacements</h4>
                  <ul className="mt-1 list-disc pl-4 text-ink-2">
                    {res.replacements.map((r) => <li key={r.fixture_id + r.key}>{r.label} ({r.match}) at {r.odds.toFixed(2)}, EV {fmtSigned(r.ev)}, confidence {r.confidence.toFixed(0)}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}
