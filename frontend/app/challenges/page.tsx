"use client";
import { useEffect, useState } from "react";
import { call } from "@/lib/client";
import type { Challenge } from "@/lib/lab-types";
import { Button, ErrorNote, Field, PageHeader, Panel, fmtSigned, inputCls } from "@/components/ui/kit";

const PRESETS: [string, "picks" | "days", number][] = [
  ["10 Picks", "picks", 10], ["30 Picks", "picks", 30], ["50 Picks", "picks", 50], ["100 Picks", "picks", 100],
  ["7-Day Challenge", "days", 7], ["30-Day Challenge", "days", 30],
];

export default function ChallengesPage() {
  const [list, setList] = useState<Challenge[]>([]);
  const [name, setName] = useState("30 Picks");
  const [kind, setKind] = useState<"picks" | "days">("picks");
  const [target, setTarget] = useState(30);
  const [minConf, setMinConf] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const load = () => call<Challenge[]>("challenges").then(setList).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function create() {
    setErr(null);
    try {
      await call("challenges", { name, kind, target, rules: minConf ? { min_confidence: Number(minConf) } : {} });
      load();
    } catch (e) { setErr((e as Error).message); }
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <PageHeader title="Challenges">
        A challenge counts the picks you track from its start. Results come from settled matches; nothing here rewards
        increasing stakes or chasing a losing run.
      </PageHeader>
      <Panel title="Start a challenge">
        <div className="flex flex-wrap gap-2">
          {PRESETS.map(([n, k, t]) => (
            <button key={n} onClick={() => { setName(n); setKind(k); setTarget(t); }} aria-pressed={name === n}
              className={`chip ${name === n ? "border-ink text-ink" : ""}`}>{n}</button>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <Field label="Name" htmlFor="c-name"><input id="c-name" value={name} onChange={(e) => setName(e.target.value)} className={inputCls} /></Field>
          <Field label="Type" htmlFor="c-kind">
            <select id="c-kind" value={kind} onChange={(e) => setKind(e.target.value as "picks" | "days")} className={inputCls}>
              <option value="picks">Number of picks</option><option value="days">Number of days</option>
            </select>
          </Field>
          <Field label="Target" htmlFor="c-target"><input id="c-target" type="number" min={1} max={1000} value={target} onChange={(e) => setTarget(Number(e.target.value))} className={`${inputCls} w-24`} /></Field>
          <Field label="Minimum confidence (optional)" htmlFor="c-conf"><input id="c-conf" value={minConf} onChange={(e) => setMinConf(e.target.value)} inputMode="numeric" className={`${inputCls} w-24`} /></Field>
          <Button primary onClick={create}>Start challenge</Button>
        </div>
        <div className="mt-2"><ErrorNote message={err} /></div>
      </Panel>
      <div className="grid gap-4 xl:grid-cols-2">
        {list.map((c) => <ChallengeCard key={c.id} c={c} />)}
      </div>
    </div>
  );
}

function ChallengeCard({ c }: { c: Challenge }) {
  const stats: [string, string][] = [
    ["Games analysed", c.games_analysed?.toString() ?? "—"], ["Picks placed", String(c.picks_placed)], ["Wins", String(c.wins)],
    ["Losses", String(c.losses)], ["Current streak", c.current_streak], ["Longest winning streak", String(c.longest_streak)],
    ["Average odds", c.average_odds?.toFixed(2) ?? "—"], ["Wagered", `${c.wagered_units.toFixed(1)}u`],
    ["Profit", `${c.profit_units >= 0 ? "+" : "−"}${Math.abs(c.profit_units).toFixed(2)}u`], ["ROI", fmtSigned(c.roi)],
  ];
  return (
    <Panel title={c.name} aside={<span className="text-xs text-muted">{c.source === "model record" ? "From the model's record" : "Yours"}, {c.status}</span>}>
      <div className="flex items-center gap-3">
        <div className="h-3 flex-1 rounded-sm bg-line/50" role="progressbar" aria-valuemin={0} aria-valuemax={c.target} aria-valuenow={c.done} aria-label={`${c.done} of ${c.target}`}>
          <div className="h-3 rounded-sm bg-home" style={{ width: `${c.progress * 100}%` }} />
        </div>
        <span className="num text-ink">{c.done} / {c.target}</span>
      </div>
      {c.pending > 0 && <p className="mt-1 text-xs text-muted">{c.pending} pick{c.pending > 1 ? "s" : ""} awaiting results</p>}
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-5">
        {stats.map(([k, v]) => <div key={k}><dt className="text-xs text-muted">{k}</dt><dd className="num text-ink">{v}</dd></div>)}
      </dl>
    </Panel>
  );
}
