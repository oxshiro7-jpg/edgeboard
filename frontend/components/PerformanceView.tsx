"use client";
import { useEffect, useState } from "react";
import { call } from "@/lib/client";
import type { BreakdownRow, CalTable, Summary } from "@/lib/lab-types";
import { BarList, CalibrationChart, EquityChart } from "./charts";
import { Button, ErrorNote, Field, Metrics, Panel, fmtPct, fmtSigned, fmtUnits, inputCls } from "./ui/kit";

interface Coverage { logged: number; settled: number; pending: number; with_closing_price: number; by_model_version: Record<string, number> }
interface Perf {
  source: string; note: string | null; available: boolean; coverage: Coverage | null; calibration: CalTable | null; summary: Summary; ev_realisation: { expected_units: number; actual_units: number; ratio: number | null; note: string | null };
  streaks: Record<string, number>; breakdowns: Record<string, BreakdownRow[]>; equity: { n: number; profit: number; expected: number }[];
}
interface Bankroll { configured: boolean; starting?: number; current?: number; peak?: number; wagered?: number; profit?: number; roi?: number | null; drawdown?: number; max_drawdown?: number; current_unit?: number; staking_note?: string }

const GROUPS: [string, string][] = [["competition", "Competition"], ["market", "Market"], ["odds_range", "Odds range"], ["confidence_range", "Confidence"],
  ["risk", "Risk"], ["home_away", "Home / away"], ["month", "Month"], ["model_version", "Model version"]];

export function PerformanceView() {
  const [p, setP] = useState<Perf | null>(null);
  const [group, setGroup] = useState("market");
  const [bank, setBank] = useState<Bankroll | null>(null);
  const [start, setStart] = useState("1000");
  const [unit, setUnit] = useState("1");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    call<Perf>("performance").then(setP).catch((e) => setErr(e.message));
    call<Bankroll>("bankroll").then(setBank).catch(() => {});
  }, []);
  if (!p) return <><ErrorNote message={err} /><p className="text-ink-2">Loading the track record.</p></>;
  const s = p.summary, st = p.streaks, cv = p.coverage;
  if (!p.available || !s.picks) {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted">Source: {p.source}.</p>
        {p.note && <p className="panel border-warn/50 p-3 text-sm text-ink">{p.note}</p>}
        <p className="text-ink-2">
          {cv ? `${cv.logged} pick${cv.logged === 1 ? "" : "s"} logged, ${cv.pending} waiting for a result. ` : ""}
          Results appear here once logged picks have been settled.
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted">Source: {p.source}.</p>
      {p.note && <p className="panel border-warn/50 p-3 text-sm text-ink">{p.note}</p>}
      {cv && (
        <p className="text-sm text-ink-2">
          {cv.logged} picks logged, {cv.settled} settled, {cv.pending} pending; {cv.with_closing_price} have a closing price.
          {" "}Model versions: {Object.entries(cv.by_model_version).map(([k, v]) => `${k} (${v})`).join(", ")}.
        </p>
      )}
      <Metrics items={[
        ["Total picks", String(s.picks)], ["Wins / losses / pushes", `${s.wins} / ${s.losses} / ${s.pushes}`], ["Win rate", fmtPct(s.win_rate)],
        ["Average odds", s.average_odds?.toFixed(2) ?? "—"], ["Profit", fmtUnits(s.profit_units)], ["ROI / yield", fmtSigned(s.roi)],
        ["Average EV at pick", fmtSigned(s.average_ev)], ["Closing line value", fmtSigned(s.clv, 2)],
      ]} />
      {p.ev_realisation.note && (
        <p className="panel border-warn/50 p-3 text-sm text-ink">{p.ev_realisation.note}</p>
      )}
      <Panel title="Closing line value">
        <Metrics items={[["Average CLV", fmtSigned(s.clv, 2)], ["Beat the closing price", fmtPct(s.beat_closing_pct)],
          ["Picks with a closing price", String(cv?.with_closing_price ?? "—")]]} />
        <p className="mt-2 text-sm text-ink-2">
          CLV compares the price when the pick was logged with the last price before kickoff. Consistently beating the
          closing price is the quickest evidence of a real edge; wins and losses take months to say the same.
        </p>
      </Panel>
      {p.calibration && (
        <Panel title="Calibration">
          <CalibrationChart rows={p.calibration.rows} />
          <p className="mt-2 text-sm text-ink-2">{p.calibration.message} Points on the diagonal mean a 70% pick wins about 70% of the time.</p>
        </Panel>
      )}
      <Panel title="Cumulative profit against expected">
        <EquityChart points={p.equity} />
        <p className="mt-2 text-sm text-ink-2">Expected line: the sum of each pick&apos;s EV at the time it was made. Actual falling well below it means the edges were overstated.</p>
      </Panel>
      <div className="grid gap-6 xl:grid-cols-2">
        <Panel title="ROI by group" aside={
          <select aria-label="Group by" value={group} onChange={(e) => setGroup(e.target.value)} className={inputCls}>
            {GROUPS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>}>
          <BarList rows={p.breakdowns[group] ?? []} value={(r: BreakdownRow) => r.roi ?? 0} format={(v) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}%`} />
        </Panel>
        <Panel title="Streaks">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            {([["Current winning streak", st.current_win], ["Current losing streak", st.current_loss], ["Longest winning streak", st.longest_win],
              ["Longest losing streak", st.longest_loss], ["Positive-EV picks in a row", st.positive_ev_current], ["Profitable days in a row", st.profitable_days_current],
              ["Profitable weeks in a row", st.profitable_weeks_current], ["Longest profitable week run", st.profitable_weeks_longest]] as [string, number][]).map(([k, v]) => (
              <div key={k}><dt className="text-xs text-muted">{k}</dt><dd className="num text-xl">{v}</dd></div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-muted">Losing streaks of this length are normal at these odds. They are no reason to raise stakes.</p>
        </Panel>
      </div>
      <Panel title="Bankroll">
        {bank?.configured ? (
          <Metrics items={[["Starting", bank.starting!.toFixed(2)], ["Current", bank.current!.toFixed(2)], ["Peak", bank.peak!.toFixed(2)],
            ["Wagered", bank.wagered!.toFixed(2)], ["Profit", bank.profit!.toFixed(2)], ["ROI", fmtSigned(bank.roi)],
            ["Drawdown", fmtPct(bank.drawdown)], ["Unit", bank.current_unit!.toFixed(2)]]} />
        ) : <p className="text-sm text-ink-2">Set a starting bankroll to track your own tracked picks with flat staking.</p>}
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <Field label="Starting bankroll" htmlFor="b-start"><input id="b-start" value={start} onChange={(e) => setStart(e.target.value)} inputMode="decimal" className={`${inputCls} w-32`} /></Field>
          <Field label="1 unit = % of bankroll" htmlFor="b-unit"><input id="b-unit" value={unit} onChange={(e) => setUnit(e.target.value)} inputMode="decimal" className={`${inputCls} w-24`} /></Field>
          <Button onClick={async () => {
            try { setBank(await call<Bankroll>("bankroll", { starting: Number(start), unit_pct: Number(unit) / 100, mode: "fixed" }, "PUT")); setErr(null); }
            catch (e) { setErr((e as Error).message); }
          }}>Save bankroll</Button>
        </div>
        <p className="mt-2 text-xs text-muted">{bank?.staking_note ?? "Flat staking. Stakes never increase after a loss."} Units are capped at 5% of bankroll.</p>
        <div className="mt-2"><ErrorNote message={err} /></div>
      </Panel>
    </div>
  );
}
