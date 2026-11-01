"use client";
import { useEffect, useState } from "react";
import type { RankedPick } from "@/lib/types";
import { PickCard } from "./PickCard";

const EXAMPLES = [
  "Confidence > 70; EV > 5%; Risk <= Moderate",
  "Odds 1.40-1.80; Status = recommended",
  "Market = Over; Confidence >= 65",
  "Competition = Premier League; EV > 4%",
];

export function FilterConsole({ initial }: { initial: RankedPick[] }) {
  const [query, setQuery] = useState("Status = recommended");
  const [picks, setPicks] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Start from the user's saved preferences (Settings page cookie), if any.
  useEffect(() => {
    const raw = document.cookie.split("; ").find((c) => c.startsWith("eb_prefs="));
    if (!raw) return;
    try {
      const p = JSON.parse(decodeURIComponent(raw.slice(9)));
      const q = `Status = recommended; Odds ${p.min_odds}-${p.max_odds}; Confidence >= ${p.min_confidence}; EV >= ${p.min_ev_pct}%`;
      setQuery(q);
      run(q);
    } catch { /* ignore malformed cookie */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run(q: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/filter", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: q }) });
      const body = await res.json();
      if (!res.ok) setError(body.detail ?? "The filter could not be applied");
      else setPicks(body.picks);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <form className="panel p-4" onSubmit={(e) => { e.preventDefault(); run(query); }}>
        <label htmlFor="q" className="text-sm font-medium">Filter selections</label>
        <p className="text-sm text-ink-2">
          Combine conditions with semicolons. Fields: confidence, EV, odds, probability, risk, edge, pick score, competition, market, status.
        </p>
        <div className="mt-2 flex gap-2">
          <input id="q" value={query} onChange={(e) => setQuery(e.target.value)} spellCheck={false}
            className="num min-w-0 flex-1 rounded border border-line bg-bg px-3 py-2 text-ink" />
          <button disabled={busy} className="rounded bg-ink px-4 py-2 text-sm font-medium text-bg disabled:opacity-60">
            {busy ? "Filtering" : "Apply filter"}
          </button>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button type="button" key={ex} onClick={() => { setQuery(ex); run(ex); }} className="chip hover:text-ink">{ex}</button>
          ))}
        </div>
        {error && <p role="alert" className="mt-2 text-sm text-ink">Filter error: {error}</p>}
      </form>
      <p className="text-sm text-ink-2">{picks.length} selection{picks.length === 1 ? "" : "s"} match.</p>
      <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
        {picks.map((p) => <PickCard key={`${p.fixture_id}${p.key}`} pick={p} />)}
      </div>
    </div>
  );
}
