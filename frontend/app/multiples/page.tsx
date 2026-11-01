"use client";
import { useEffect, useState } from "react";
import { call } from "@/lib/client";
import type { BuildResult, Multiple, OptimiseResult } from "@/lib/lab-types";
import { useSlip } from "@/components/SlipContext";
import { MultipleView, OptimiseView } from "@/components/MultipleView";
import { Button, ErrorNote, Field, PageHeader, Panel, inputCls } from "@/components/ui/kit";

const MODES = ["conservative", "balanced", "value", "aggressive"] as const;

export default function MultiplesPage() {
  const slip = useSlip();
  const [legs, setLegs] = useState(3);
  const [custom, setCustom] = useState("");
  const [mode, setMode] = useState<(typeof MODES)[number]>("balanced");
  const [fromSlipMatches, setFromSlipMatches] = useState(false);
  const [built, setBuilt] = useState<BuildResult | null>(null);
  const [current, setCurrent] = useState<Multiple | null>(null);
  const [opt, setOpt] = useState<OptimiseResult | null>(null);
  const [alts, setAlts] = useState<BuildResult[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { call<BuildResult[]>("multiples/alternatives").then(setAlts).catch((e) => setErr(e.message)); }, []);
  useEffect(() => {
    setOpt(null);
    if (!slip.legs.length) { setCurrent(null); return; }
    call<Multiple>("multiples/evaluate", { legs: slip.legs.map(({ fixture_id, market_key }) => ({ fixture_id, market_key })) })
      .then(setCurrent).catch((e) => setErr(e.message));
  }, [slip.legs]);

  async function build() {
    setBusy(true); setErr(null);
    try {
      const n = custom ? Number(custom) : legs;
      const fixtures = fromSlipMatches ? Array.from(new Set(slip.legs.map((l) => l.fixture_id))) : undefined;
      setBuilt(await call<BuildResult>("multiples/build", { legs: n, mode, fixtures }));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  async function optimise() {
    setBusy(true); setErr(null);
    try { setOpt(await call<OptimiseResult>("multiples/optimise", { legs: slip.legs.map(({ fixture_id, market_key }) => ({ fixture_id, market_key })) })); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  const applyMultiple = (m: Multiple) => slip.set(m.legs.map((l) => ({ fixture_id: l.fixture_id, market_key: l.key, label: l.label, match: l.match, odds: l.odds })));

  return (
    <div className="flex flex-col gap-6 p-6">
      <PageHeader title="Multiples">
        Legs on the same match are priced jointly from that match&apos;s score distribution, so correlated selections are
        not multiplied as if independent. Every extra leg also compounds the bookmaker margin.
      </PageHeader>
      <ErrorNote message={err} />

      <Panel title="Build a multiple">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Legs" htmlFor="legs">
            <div id="legs" role="group" className="flex gap-1">
              {[2, 3, 4, 5].map((n) => (
                <button key={n} onClick={() => { setLegs(n); setCustom(""); }} aria-pressed={!custom && legs === n}
                  className={`rounded border px-3 py-1.5 text-sm ${!custom && legs === n ? "border-ink text-ink" : "border-line text-ink-2"}`}>{n}</button>
              ))}
              <input aria-label="Custom number of legs" placeholder="Custom" value={custom} inputMode="numeric"
                onChange={(e) => setCustom(e.target.value.replace(/\D/g, "").slice(0, 2))} className={`${inputCls} w-20`} />
            </div>
          </Field>
          <Field label="Mode" htmlFor="mode">
            <select id="mode" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} className={inputCls}>
              {MODES.map((m) => <option key={m} value={m}>{m[0].toUpperCase() + m.slice(1)}</option>)}
            </select>
          </Field>
          <label className="flex items-center gap-2 pb-2 text-sm text-ink-2">
            <input type="checkbox" checked={fromSlipMatches} onChange={(e) => setFromSlipMatches(e.target.checked)} disabled={!slip.legs.length} />
            Only matches already in my multiple
          </label>
          <Button primary onClick={build} disabled={busy} className="px-6 py-2.5 text-base">Build multiple</Button>
        </div>
        {built && (
          <div className="mt-5 border-t border-line pt-4">
            {built.built && built.multiple ? (
              <>
                <MultipleView m={built.multiple} title={`${built.mode[0].toUpperCase() + built.mode.slice(1)} ${built.requested_legs}-leg multiple`} />
                <Button className="mt-3" onClick={() => applyMultiple(built.multiple!)}>Use as my multiple</Button>
              </>
            ) : <p className="text-ink-2">{built.reason}</p>}
          </div>
        )}
      </Panel>

      <Panel title="My multiple" aside={slip.legs.length > 0 && <Button onClick={slip.clear}>Clear</Button>}>
        {current ? (
          <>
            <MultipleView m={current} />
            <div className="mt-3 flex flex-wrap gap-2">
              <Button primary onClick={optimise} disabled={busy || slip.legs.length < 3}>Optimise multiple</Button>
              {slip.legs.map((l) => <Button key={l.fixture_id + l.market_key} onClick={() => slip.remove(l.fixture_id, l.market_key)}>Remove {l.label}</Button>)}
            </div>
            {opt && (
              <div className="mt-5 border-t border-line pt-4">
                <OptimiseView r={opt} />
                {opt.removed.length > 0 && <Button className="mt-3" onClick={() => applyMultiple(opt.optimised)}>Apply optimised multiple</Button>}
              </div>
            )}
          </>
        ) : <p className="text-ink-2">Add selections from Best picks or a match page, or build one above.</p>}
      </Panel>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Alternatives today</h2>
        <div className="grid gap-4 xl:grid-cols-2">
          {alts.map((a) => (
            <Panel key={a.name} title={a.name}>
              {a.built && a.multiple ? (
                <><MultipleView m={a.multiple} /><Button className="mt-3" onClick={() => applyMultiple(a.multiple!)}>Use this multiple</Button></>
              ) : <p className="text-sm text-ink-2">{a.reason}</p>}
            </Panel>
          ))}
        </div>
      </section>
    </div>
  );
}
