"use client";
import { useState } from "react";
import { call } from "@/lib/client";
import type { BreakdownRow, CalTable } from "@/lib/lab-types";
import { BarList, CalibrationChart, EquityChart } from "@/components/charts";
import { Button, ErrorNote, Field, Metrics, PageHeader, Panel, fmtPct, fmtSigned, fmtUnits, inputCls } from "@/components/ui/kit";

interface BT {
  matches: number;
  summary: Record<string, number | null>;
  equity: { n: number; profit: number; expected: number }[];
  calibration_selected: CalTable; calibration_all: CalTable;
  result_forecast: { model: { n: number; brier: number; log_loss: number }; market: { n: number; brier: number; log_loss: number } };
  breakdowns: Record<string, BreakdownRow[]>;
  ev_realisation: { note: string | null; ratio: number | null };
}

const FAMILIES = ["result", "goals", "btts", "handicap", "first_half", "second_half", "cards", "corners"];

export default function BacktestingPage() {
  const [f, setF] = useState({ date_from: "", date_to: "", min_odds: "1.20", max_odds: "6.00", min_confidence: "60", min_ev: "4",
    max_risk: "High", selection: "recommended", families: [] as string[] });
  const [r, setR] = useState<BT | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: string) => setF({ ...f, [k]: v });

  async function run() {
    setBusy(true); setErr(null);
    try {
      setR(await call<BT>("backtest", {
        date_from: f.date_from ? `${f.date_from}T00:00:00Z` : null, date_to: f.date_to ? `${f.date_to}T00:00:00Z` : null,
        min_odds: Number(f.min_odds), max_odds: Number(f.max_odds), min_confidence: Number(f.min_confidence),
        min_ev: f.min_ev === "" ? null : Number(f.min_ev) / 100, max_risk: f.max_risk, selection: f.selection, families: f.families,
      }));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  const s = r?.summary;
  return (
    <div className="flex flex-col gap-6 p-6">
      <PageHeader title="Backtesting lab">
        Walk-forward: every historical match is re-analysed with only the data available two hours before kickoff, bet at
        the price then, and compared with the closing price.
      </PageHeader>
      <Panel title="Test settings">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="From" htmlFor="b-from"><input id="b-from" type="date" value={f.date_from} onChange={(e) => set("date_from", e.target.value)} className={inputCls} /></Field>
          <Field label="To" htmlFor="b-to"><input id="b-to" type="date" value={f.date_to} onChange={(e) => set("date_to", e.target.value)} className={inputCls} /></Field>
          <Field label="Min odds" htmlFor="b-mino"><input id="b-mino" value={f.min_odds} onChange={(e) => set("min_odds", e.target.value)} className={`${inputCls} w-20`} /></Field>
          <Field label="Max odds" htmlFor="b-maxo"><input id="b-maxo" value={f.max_odds} onChange={(e) => set("max_odds", e.target.value)} className={`${inputCls} w-20`} /></Field>
          <Field label="Min confidence" htmlFor="b-conf"><input id="b-conf" value={f.min_confidence} onChange={(e) => set("min_confidence", e.target.value)} className={`${inputCls} w-20`} /></Field>
          <Field label="Min EV %" htmlFor="b-ev"><input id="b-ev" value={f.min_ev} onChange={(e) => set("min_ev", e.target.value)} className={`${inputCls} w-20`} /></Field>
          <Field label="Max risk" htmlFor="b-risk">
            <select id="b-risk" value={f.max_risk} onChange={(e) => set("max_risk", e.target.value)} className={inputCls}>
              {["Very Low", "Low", "Moderate", "High", "Very High"].map((x) => <option key={x}>{x}</option>)}
            </select>
          </Field>
          <Field label="Selections" htmlFor="b-sel">
            <select id="b-sel" value={f.selection} onChange={(e) => set("selection", e.target.value)} className={inputCls}>
              <option value="recommended">Every recommended pick</option>
              <option value="best_per_match">Best pick per match</option>
              <option value="all_priced">Every priced selection passing the filters</option>
            </select>
          </Field>
        </div>
        <fieldset className="mt-3 flex flex-wrap gap-2">
          <legend className="mb-1 text-xs text-muted">Markets (none selected = all)</legend>
          {FAMILIES.map((x) => (
            <label key={x} className="chip cursor-pointer">
              <input type="checkbox" checked={f.families.includes(x)} className="mr-1"
                onChange={(e) => setF({ ...f, families: e.target.checked ? [...f.families, x] : f.families.filter((y) => y !== x) })} />
              {x.replace("_", " ")}
            </label>
          ))}
        </fieldset>
        <Button primary className="mt-4" onClick={run} disabled={busy}>{busy ? "Running backtest" : "Run backtest"}</Button>
        <div className="mt-2"><ErrorNote message={err} /></div>
      </Panel>
      {r && s && (
        <>
          <Metrics items={[
            ["Matches", String(r.matches)], ["Bets", String(s.bets)], ["Hit rate", fmtPct(s.hit_rate)], ["ROI", fmtSigned(s.roi)],
            ["Profit", fmtUnits(s.profit_units)], ["Max drawdown", `${(s.max_drawdown_units ?? 0).toFixed(1)}u`],
            ["Average odds", s.average_odds?.toFixed(2) ?? "—"], ["Expected", fmtUnits(s.expected_profit_units)], ["Actual", fmtUnits(s.actual_profit_units)],
            ["Sharpe-like", s.sharpe_like?.toFixed(3) ?? "—", "Mean profit per bet divided by its standard deviation"],
            ["Longest losing streak", String(s.longest_losing_streak)], ["Closing line value", fmtSigned(s.clv, 2)],
          ]} />
          {r.ev_realisation.note && <p className="panel border-warn/50 p-3 text-sm">{r.ev_realisation.note}</p>}
          <Panel title="Profit against expected"><EquityChart points={r.equity} /></Panel>
          <div className="grid gap-6 xl:grid-cols-2">
            <Panel title="Calibration of selected bets">
              <CalibrationChart rows={r.calibration_selected.rows} />
              <p className="mt-2 text-sm text-ink-2">{r.calibration_selected.message}</p>
            </Panel>
            <Panel title="1X2 forecast quality">
              <table className="w-full text-sm">
                <thead><tr className="border-b border-line"><th className="th pl-0" /><th className="th text-right">Log loss</th><th className="th text-right">Brier</th></tr></thead>
                <tbody>
                  <tr className="border-b border-line/60"><td className="py-1.5">Model</td><td className="num text-right">{r.result_forecast.model.log_loss}</td><td className="num text-right">{r.result_forecast.model.brier}</td></tr>
                  <tr><td className="py-1.5">Market (no-vig)</td><td className="num text-right">{r.result_forecast.market.log_loss}</td><td className="num text-right">{r.result_forecast.market.brier}</td></tr>
                </tbody>
              </table>
              <p className="mt-2 text-sm text-ink-2">Lower is better. A model that cannot beat the de-margined market here should not be trusted to find value in the 1X2.</p>
              <h3 className="mt-4 text-sm font-medium">ROI by market</h3>
              <div className="mt-2"><BarList rows={r.breakdowns.market ?? []} value={(x: BreakdownRow) => x.roi ?? 0} format={(v) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}%`} /></div>
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
