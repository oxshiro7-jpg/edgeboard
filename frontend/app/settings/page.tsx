"use client";
import { useEffect, useState, type FormEvent } from "react";

// Stored in a first-party cookie for now; moves to /api/me/settings once
// sign-in ships in the UI (the backend endpoints already exist).
type Prefs = {
  odds_format: "decimal" | "fractional"; min_odds: number; max_odds: number;
  min_confidence: number; min_ev_pct: number; default_multiple_size: number;
};
const DEFAULTS: Prefs = { odds_format: "decimal", min_odds: 1.2, max_odds: 6, min_confidence: 60, min_ev_pct: 4, default_multiple_size: 3 };

export default function SettingsPage() {
  const [prefs, setPrefs] = useState<Prefs>(DEFAULTS);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const raw = document.cookie.split("; ").find((c) => c.startsWith("eb_prefs="));
    if (raw) {
      try { setPrefs({ ...DEFAULTS, ...JSON.parse(decodeURIComponent(raw.slice(9))) }); } catch { /* ignore bad cookie */ }
    }
  }, []);

  function save(e: FormEvent) {
    e.preventDefault();
    document.cookie = `eb_prefs=${encodeURIComponent(JSON.stringify(prefs))}; path=/; max-age=31536000; samesite=lax`;
    setSaved(true);
  }

  const num = (k: keyof Prefs, label: string, step: string, min: number, max: number) => (
    <label className="flex flex-col gap-1 text-sm text-ink-2">
      {label}
      <input type="number" step={step} min={min} max={max} value={prefs[k] as number}
        onChange={(e) => { setSaved(false); setPrefs({ ...prefs, [k]: Number(e.target.value) }); }}
        className="num rounded border border-line bg-bg px-3 py-2 text-ink" />
    </label>
  );

  return (
    <div className="max-w-2xl p-6">
      <h1 className="font-cond text-3xl font-semibold tracking-tight">Settings</h1>
      <form onSubmit={save} className="panel mt-4 grid gap-4 p-5 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm text-ink-2 sm:col-span-2">
          Odds format
          <select value={prefs.odds_format} onChange={(e) => { setSaved(false); setPrefs({ ...prefs, odds_format: e.target.value as Prefs["odds_format"] }); }}
            className="rounded border border-line bg-bg px-3 py-2 text-ink">
            <option value="decimal">Decimal (1.80)</option>
            <option value="fractional">Fractional (4/5)</option>
          </select>
        </label>
        {num("min_odds", "Minimum odds", "0.01", 1.01, 100)}
        {num("max_odds", "Maximum odds", "0.01", 1.01, 1000)}
        {num("min_confidence", "Minimum confidence", "1", 0, 100)}
        {num("min_ev_pct", "Minimum EV (%)", "0.5", -50, 100)}
        {num("default_multiple_size", "Default multiple size", "1", 2, 10)}
        <div className="flex items-center gap-3 sm:col-span-2">
          <button className="rounded bg-ink px-4 py-2 text-sm font-medium text-bg">Save settings</button>
          {saved && <span role="status" className="text-sm text-ink-2">Settings saved</span>}
        </div>
      </form>
      <p className="mt-4 text-sm text-muted">
        Your odds range, confidence and EV minimums become the default query in the Best picks filter.
        Engine thresholds, model weights and the competition whitelist are admin settings, edited through
        /api/admin/config.
      </p>
    </div>
  );
}
